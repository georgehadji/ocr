"""OmniOCR Review UI — Streamlit correction interface.

Run from the repository root:

    pip install -e ".[pdf,kraken,opencv,docx]"
    streamlit run editions/desktop/review_ui.py

Keyboard shortcuts:
    ← →        prev/next page
    Ctrl+S     save session
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import streamlit as st
from PIL import Image

from omniocr.application.metrics import character_error_rate
from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.application.router import ScriptRouter
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.infrastructure.review import (
    ReviewDocument,
    build_review_document,
    group_suggestions_by_reason,
)

st.set_page_config(page_title="OmniOCR Review", layout="wide")

# ---------- Polytonic keyboard ----------

POLYTONIC_MAP: dict[str, str] = {
    "a>": "ἀ", "a<": "ἁ", "a/>": "ἄ", "a/<": "ἅ",
    "a=>": "ἂ", "a=<": "ἃ", "a~>": "ἆ", "a~<": "ἇ",
    "A>": "Ἀ", "A<": "Ἁ", "A/>": "Ἄ", "A/<": "Ἅ",
    "e>": "ἐ", "e<": "ἑ", "e/>": "ἔ", "e/<": "ἕ",
    "E>": "Ἐ", "E<": "Ἑ",
    "h>": "ἠ", "h<": "ἡ", "h/>": "ἤ", "h/<": "ἥ",
    "h~>": "ἦ", "h~<": "ἧ", "H>": "Ἠ", "H<": "Ἡ",
    "i>": "ἰ", "i<": "ἱ", "i/>": "ἴ", "i/<": "ἵ",
    "i~>": "ἶ", "i~<": "ἷ", "I>": "Ἰ", "I<": "Ἱ",
    "o>": "ὀ", "o<": "ὁ", "o/>": "ὄ", "o/<": "ὅ",
    "O>": "Ὀ", "O<": "Ὁ",
    "u>": "ὐ", "u<": "ὑ", "u/>": "ὔ", "u/<": "ὕ", "U<": "Ὑ",
    "w>": "ὠ", "w<": "ὡ", "w/>": "ὤ", "w/<": "ὥ",
    "w~>": "ὦ", "w~<": "ὧ", "W>": "Ὠ", "W<": "Ὡ",
    "r>": "ῤ", "r<": "ῥ", "R<": "Ῥ",
    "'": "᾽",
}

POLYTONIC_GROUPS: list[tuple[str, list[str]]] = [
    ("Alpha", ["a>", "a<", "a/>", "a/<", "a=>", "a=<", "a~>", "a~<"]),
    ("Epsilon", ["e>", "e<", "e/>", "e/<"]),
    ("Eta", ["h>", "h<", "h/>", "h/<", "h~>", "h~<"]),
    ("Iota", ["i>", "i<", "i/>", "i/<", "i~>", "i~<"]),
    ("Omicron", ["o>", "o<", "o/>", "o/<"]),
    ("Upsilon", ["u>", "u<", "u/>", "u/<"]),
    ("Omega", ["w>", "w<", "w/>", "w/<", "w~>", "w~<"]),
    ("Rho", ["r>", "r<"]),
    ("Uppercase", ["A>", "A<", "A/>", "A/<", "E>", "E<", "H>", "H<", "I>", "I<", "O>", "O<", "U<", "W>", "W<", "R<"]),
]


# ---------- Helpers ----------

SESSION_DIR = Path.home() / ".omniocr" / "sessions"


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _save_session(file_name: str, file_data: bytes) -> None:
    """Persist ground truth and edits to ~/.omniocr/sessions/{hash}.json."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fhash = _file_hash(file_data)
    session = {
        "file_hash": fhash,
        "file_name": file_name,
        "current_page": st.session_state.current_page,
        "ground_truth_lines": dict(st.session_state.ground_truth_lines),
        "edited_lines": dict(st.session_state.edited_lines),
    }
    (SESSION_DIR / f"{fhash}.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_session(file_data: bytes) -> dict | None:
    """Load a saved session for the given file, or None."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fhash = _file_hash(file_data)
    path = SESSION_DIR / f"{fhash}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# ---------- Session state ----------

for key, default in [
    ("review_document", None),
    ("current_page", 0),
    ("ground_truth_lines", {}),
    ("edited_lines", {}),
    ("file_bytes", b""),
    ("file_name", ""),
    ("saved", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------- Sidebar ----------

st.sidebar.title("OmniOCR Review")
st.sidebar.caption("Scholarly correction interface")

uploaded_file = st.sidebar.file_uploader(
    "Upload a PDF or image",
    type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
    help="Supports up to 2 GB files.",
)

# ---------- Pipeline execution ----------

if uploaded_file is not None and st.session_state.review_document is None:
    pdf_bytes = uploaded_file.read()
    file_name = uploaded_file.name
    st.session_state.file_bytes = pdf_bytes
    st.session_state.file_name = file_name

    # Check for saved session
    session = _load_session(pdf_bytes)
    if session:
        st.session_state.ground_truth_lines = session.get("ground_truth_lines", {})
        st.session_state.edited_lines = session.get("edited_lines", {})
        st.sidebar.success(f"Restored session with {len(session['ground_truth_lines'])} corrections")

    with st.spinner("Initializing OCR pipeline…") if not session else st.spinner():
        cfg = Settings.from_env()
        try:
            from omniocr.infrastructure.ingest import DocumentPageSource
            from omniocr.infrastructure.preprocess import GrayscaleProcessor
            from omniocr.infrastructure.kraken import KrakenLayoutAnalyzer
            page_source = DocumentPageSource()
            image_processor = GrayscaleProcessor()
            layout = KrakenLayoutAnalyzer(Script.POLYTONIC)
        except ImportError as exc:
            st.error(f"Missing dependency: {exc}. Install: pip install -e '.[pdf,kraken,opencv]'")
            st.stop()

        pipeline_kwargs: dict = {
            "page_source": page_source,
            "image_processor": image_processor,
            "layout_analyzer": layout,
            "post_corrector": SuggestOnlyCorrector(lexicons=lexicons_by_script()),
            "exporter": MarkdownExporter(),
        }
        if cfg.enable_vlm and cfg.vlm_api_key:
            from omniocr.infrastructure.vlm import VLMEngine
            pipeline_kwargs["router"] = ScriptRouter(
                by_script={
                    Script.ANCIENT: (VLMEngine(cfg.vlm_api_key),),
                    Script.BYZANTINE: (VLMEngine(cfg.vlm_api_key),),
                    Script.POLYTONIC: (VLMEngine(cfg.vlm_api_key),),
                },
                default=(),
            )
            st.sidebar.info(f"VLM enabled: {cfg.vlm_api_key[:12]}…")

        pipeline = PipelineOrchestrator(**pipeline_kwargs)
        ctx = TenantContext("desktop", "reviewer", "desktop")
        total = pipeline.count_pages(pdf_bytes)
        progress = st.sidebar.progress(0, f"OCR in progress — 0/{total} pages")
        pages: list = []

        for page_num, page in pipeline.run_iteratively(pdf_bytes, ctx):
            pages.append(page)
            if page.failures:
                st.sidebar.warning(f"Page {page_num} failed: {page.failures[0].message}")
            progress.progress(page_num / total, f"OCR in progress — {page_num}/{total} pages")

        progress.progress(1.0, "Complete — building review document")

        page_images = [b""] * len(pages)
        try:
            import fitz
            source = fitz.open(stream=pdf_bytes, filetype="pdf")
            for i in range(min(len(pages), len(source))):
                pix = source[i].get_pixmap(dpi=150)
                page_images[i] = pix.tobytes("png")
            source.close()
        except ImportError:
            pass

        # Build review document from pipeline output
        from omniocr.domain.models import DocumentPage, DocumentStructure
        doc_struct = DocumentStructure(pages=tuple(pages))
        st.session_state.review_document = build_review_document(doc_struct, page_images)
        st.session_state.current_page = 0
        progress.empty()
        st.rerun()

# ---------- Review UI ----------

if st.session_state.review_document is not None:
    doc = st.session_state.review_document
    total_pages = len(doc.pages)
    file_name = st.session_state.file_name
    file_bytes = st.session_state.file_bytes

    # --- Sidebar controls ---
    st.sidebar.divider()
    st.sidebar.caption(f"📄 {file_name} · 📊 {total_pages} pages")

    failed_pages = sum(1 for p in doc.pages if p.failures)
    if failed_pages:
        st.sidebar.caption(f"⚠️ {failed_pages} failed")

    col1, col2, col3 = st.sidebar.columns([3, 1, 1])
    with col1:
        page_jump = st.number_input("Page", 1, total_pages, st.session_state.current_page + 1, label_visibility="collapsed")
        if page_jump != st.session_state.current_page + 1:
            st.session_state.current_page = page_jump - 1
            st.rerun()
    with col2:
        if st.button("◀", disabled=st.session_state.current_page == 0):
            st.session_state.current_page -= 1
            st.rerun()
    with col3:
        if st.button("▶", disabled=st.session_state.current_page >= total_pages - 1):
            st.session_state.current_page += 1
            st.rerun()

    st.sidebar.caption(f"Page {st.session_state.current_page + 1} of {total_pages}")

    # --- Suggestion summary ---
    st.sidebar.divider()
    summary = group_suggestions_by_reason(doc)
    total_suggestions = sum(summary.values()) if summary else 0
    low_conf = sum(1 for p in doc.pages for l in p.lines if l.is_low_confidence)

    if summary:
        st.sidebar.subheader(f"💡 {total_suggestions} Suggestions")
        for reason, count in sorted(summary.items(), key=lambda x: -x[1]):
            pct = int(count / total_suggestions * 100) if total_suggestions else 0
            st.sidebar.write(f"  {reason}: {count} ({pct}%)")

    # --- Batch actions ---
    st.sidebar.subheader("⚡ Batch actions")
    if st.sidebar.button("Accept all reversible", use_container_width=True, type="primary"):
        accepted = 0
        for page in doc.pages:
            for line in page.lines:
                for s in line.suggestions:
                    if s.reversible and s.suggestion_text:
                        st.session_state.ground_truth_lines[s.line_id] = s.suggestion_text
                        accepted += 1
        st.sidebar.success(f"Accepted {accepted} suggestions")
        st.rerun()

    if st.sidebar.button("Clear all corrections", use_container_width=True):
        st.session_state.ground_truth_lines.clear()
        st.session_state.edited_lines.clear()
        st.rerun()

    # --- Save session ---
    st.sidebar.divider()
    if st.sidebar.button("💾 Save session", use_container_width=True):
        _save_session(file_name, file_bytes)
        st.sidebar.success("Session saved")
    if st.sidebar.button("↩ Undo last action"):
        if st.session_state.ground_truth_lines:
            last_key = list(st.session_state.ground_truth_lines.keys())[-1]
            del st.session_state.ground_truth_lines[last_key]
            st.rerun()
        elif st.session_state.edited_lines:
            last_key = list(st.session_state.edited_lines.keys())[-1]
            del st.session_state.edited_lines[last_key]
            st.rerun()

    # --- Polytonic keyboard ---
    st.sidebar.divider()
    st.sidebar.subheader("⌨ Polytonic Keyboard")
    for label, combos in POLYTONIC_GROUPS:
        with st.sidebar.expander(label):
            for combo in combos:
                glyph = POLYTONIC_MAP.get(combo, "")
                if glyph:
                    if st.sidebar.button(f"{combo} → {glyph}", key=f"pb_{combo}", use_container_width=True):
                        st.session_state.pb_last = glyph
                        st.rerun()
    if st.session_state.get("pb_last"):
        st.sidebar.info(f"Last glyph: **{st.session_state.pb_last}** (Ctrl+V to paste)")

    # --- Export ---
    st.sidebar.divider()
    st.sidebar.subheader("📥 Export")

    ctx = TenantContext("desktop", "export", "desktop")

    export_md = "\n\n".join(
        f"## Page {p.number}\n\n" + "\n".join(
            st.session_state.ground_truth_lines.get(l.line_id, st.session_state.edited_lines.get(l.line_id, l.text))
            for l in p.lines
        )
        for p in doc.pages
    )
    export_txt = "\n".join(
        st.session_state.ground_truth_lines.get(l.line_id, st.session_state.edited_lines.get(l.line_id, l.text))
        for p in doc.pages
        for l in p.lines
    )

    base = Path(file_name).stem
    col_md, col_txt = st.sidebar.columns(2)
    with col_md:
        st.download_button("📄 .md", export_md.encode("utf-8"), f"{base}.md", use_container_width=True)
    with col_txt:
        st.download_button("📄 .txt", export_txt.encode("utf-8"), f"{base}.txt", use_container_width=True)

    # Ground truth summary
    if st.session_state.ground_truth_lines:
        st.sidebar.divider()
        with st.sidebar.expander(f"✅ {len(st.session_state.ground_truth_lines)} accepted"):
            for lid, text in st.session_state.ground_truth_lines.items():
                orig = next((l.text for p in doc.pages for l in p.lines if l.line_id == lid), "")
                cer = character_error_rate(orig, text)
                st.write(f"**{lid[:20]}**: {text[:40]}… *(CER: {cer:.3f})*" if len(text) > 40 else f"**{lid[:20]}**: {text}")

# ---------- Main review pane ----------

if st.session_state.review_document is not None:
    doc = st.session_state.review_document
    page_idx = st.session_state.current_page
    if 0 <= page_idx < len(doc.pages):
        page = doc.pages[page_idx]

        image_col, text_col = st.columns([3, 2])

        with image_col:
            if page.image_bytes:
                st.image(Image.open(io.BytesIO(page.image_bytes)), use_container_width=True)
            else:
                st.info("Page image — install PyMuPDF: pip install -e '.[pdf]'")

        with text_col:
            st.subheader(f"Page {page.number}")
            for f in page.failures:
                st.error(f"Failed: {f.message}")

            for line in page.lines:
                display = st.session_state.ground_truth_lines.get(line.line_id, st.session_state.edited_lines.get(line.line_id, line.text))
                changed = display != line.text
                conf = line.confidence

                if conf < 40:
                    badge = "🔴"
                    bar = "#ff4444"
                elif conf < 60:
                    badge = "🟡"
                    bar = "#ffaa00"
                elif conf < 80:
                    badge = "🟢"
                    bar = "#44aa44"
                else:
                    badge = "✅"
                    bar = "#228822"

                st.markdown(
                    f"""<div style="border-left:4px solid {bar};padding:4px 8px 0;margin:2px 0;background:linear-gradient(90deg,{bar}08,transparent)">
                    <span style="color:{bar};font-weight:bold">{badge} {conf:.0f}%</span>
                    {"<span style='background:#22882222;text-decoration:underline;text-decoration-color:#228822'>" if changed else ""}
                    {display}
                    {"</span>" if changed else ""}
                    </div>""",
                    unsafe_allow_html=True,
                )

                with st.expander("✏️ Edit / suggestions"):
                    new_text = st.text_area("Line", display, key=f"ta_{line.line_id}", label_visibility="collapsed")
                    if new_text != display:
                        if new_text != line.text:
                            st.session_state.edited_lines[line.line_id] = new_text
                        else:
                            st.session_state.edited_lines.pop(line.line_id, None)
                        st.rerun()

                    for s in line.suggestions:
                        akey = f"a_{s.line_id}_{s.reason}"
                        if s.suggestion_text:
                            st.write(f"**{s.reason}**: {s.suggestion_text}")
                        else:
                            st.write(f"**{s.reason}**: flag only")
                        if s.reversible and s.suggestion_text:
                            if st.button("Accept", key=akey):
                                st.session_state.ground_truth_lines[s.line_id] = s.suggestion_text
                                st.rerun()

                    with st.popover("⌨ Insert polytonic"):
                        for combo, glyph in POLYTONIC_MAP.items():
                            if st.button(f"{combo} → {glyph}", key=f"in_{line.line_id}_{combo}"):
                                current = st.session_state.ground_truth_lines.get(line.line_id, st.session_state.edited_lines.get(line.line_id, line.text))
                                st.session_state.edited_lines[line.line_id] = current + glyph
                                st.rerun()

                st.caption(f"{line.script} · {line.region_type} · #{line.reading_order}")

            if st.session_state.ground_truth_lines:
                with st.expander(f"✅ {len(st.session_state.ground_truth_lines)} corrections"):
                    for lid, text in st.session_state.ground_truth_lines.items():
                        orig = next((l.text for p in doc.pages for l in p.lines if l.line_id == lid), "")
                        cer = character_error_rate(orig, text)
                        st.write(f"**{lid[:20]}**: {text} *(CER: {cer:.3f})*")

else:
    st.info("Upload a PDF or image to begin review")
