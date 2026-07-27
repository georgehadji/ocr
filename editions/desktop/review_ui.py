"""OmniOCR Review UI — Streamlit correction interface.

Run from the repository root:

    streamlit run editions/desktop/review_ui.py

Requires: streamlit, Pillow, and the optional extras.

    pip install -e ".[pdf,kraken,opencv,docx]"

Keyboard shortcuts:
    ← →        prev/next page
    Ctrl+Enter  jump to page number in input
"""

from __future__ import annotations

import io
from pathlib import Path

import streamlit as st
from PIL import Image

from omniocr.application.metrics import character_error_rate
from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.application.router import ScriptRouter
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.exporters import DocxExporter, MarkdownExporter
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
    "'": "᾽",  # koronis
}

# Keyboard category groups for the persistent panel
POLYTONIC_GROUPS: list[tuple[str, dict[str, str]]] = [
    ("Breathings", {"Smooth (>)": ">", "Rough (<)": "<"}),
    ("Accents", {"Acute (/)": "/", "Grave (=)": "=", "Circumflex (~)": "~"}),
    ("Combos", {k: k for k in ["a>", "a<", "a/>", "a/<", "e>", "e<", "e/>", "e/<",
                                "h>", "h<", "h/>", "h/<", "o>", "o<", "o/>", "o/<",
                                "w>", "w<", "w/>", "w/<"]}),
]

# ---------- Session state ----------

if "review_document" not in st.session_state:
    st.session_state.review_document: ReviewDocument | None = None
if "current_page" not in st.session_state:
    st.session_state.current_page = 0
if "ground_truth_lines" not in st.session_state:
    st.session_state.ground_truth_lines: dict[str, str] = {}
if "edited_lines" not in st.session_state:
    st.session_state.edited_lines: dict[str, str] = {}
if "pb_pending" not in st.session_state:
    st.session_state.pb_pending: str = ""


# ---------- Keyboard shortcut injection ----------

st.markdown(
    """
<script>
document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const streamlitDoc = window.parent.document;
    if (e.key === 'ArrowLeft') {
        const btn = streamlitDoc.querySelector('[data-testid="stButton"] button:has-text("◀")');
        if (btn) btn.click();
    } else if (e.key === 'ArrowRight') {
        const btn = streamlitDoc.querySelector('[data-testid="stButton"] button:has-text("▶")');
        if (btn) btn.click();
    }
});
</script>
""",
    unsafe_allow_html=True,
)

# ---------- Sidebar controls ----------

st.sidebar.title("OmniOCR Review")
st.sidebar.caption("Scholarly correction interface")

uploaded_file = st.sidebar.file_uploader(
    "Upload a PDF or image",
    type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
    help="Supports up to 2 GB files.",
)

if uploaded_file is not None and st.session_state.review_document is None:
    file_name = uploaded_file.name
    with st.spinner(f"Running OCR on {file_name}..."):
        pdf_bytes = uploaded_file.read()
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
            st.sidebar.info(f"VLM enabled: {cfg.vlm_api_key[:12]}...")

        pipeline = PipelineOrchestrator(**pipeline_kwargs)
        ctx = TenantContext("desktop", "reviewer", "desktop")

        with st.spinner("Processing pages..."):
            progress = st.sidebar.progress(0, "OCR in progress…")
            result = pipeline.run(pdf_bytes, ctx)
            progress.progress(100, "Complete")

        if result.is_ok():
            pages = list(result.value.pages)
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
            st.session_state.review_document = build_review_document(result.value, page_images)
            st.session_state.current_page = 0
            st.session_state.file_name = file_name
            st.rerun()
        else:
            st.sidebar.error(f"Pipeline failed: {result.error}")

if st.session_state.review_document is not None:
    doc = st.session_state.review_document
    total_pages = len(doc.pages)
    file_name = st.session_state.get("file_name", "document.pdf")

    st.sidebar.divider()

    # Page navigation — number input + arrow buttons
    nav_col1, nav_col2, nav_col3 = st.sidebar.columns([3, 1, 1])
    with nav_col1:
        page_jump = st.number_input(
            "Page",
            min_value=1,
            max_value=total_pages,
            value=st.session_state.current_page + 1,
            label_visibility="collapsed",
            key="page_jump",
        )
        if page_jump != st.session_state.current_page + 1:
            st.session_state.current_page = page_jump - 1
            st.rerun()
    with nav_col2:
        if st.button("◀", help="Previous page (←)", disabled=st.session_state.current_page == 0):
            st.session_state.current_page -= 1
            st.rerun()
    with nav_col3:
        if st.button("▶", help="Next page (→)", disabled=st.session_state.current_page == total_pages - 1):
            st.session_state.current_page += 1
            st.rerun()

    st.sidebar.caption(f"of {total_pages} pages")
    st.sidebar.divider()

    # Document info
    st.sidebar.caption(f"📄 File: {file_name}")
    st.sidebar.caption(f"📊 {total_pages} pages")
    failed_pages = sum(1 for p in doc.pages if p.failures)
    if failed_pages:
        st.sidebar.caption(f"⚠️ {failed_pages} failed pages")

    # Suggestion summary
    summary = group_suggestions_by_reason(doc)
    if summary:
        st.sidebar.subheader("Suggestions")
        for reason, count in sorted(summary.items(), key=lambda x: -x[1]):
            st.sidebar.write(f"  {reason}: {count}")

    # Persistent polytonic keyboard
    st.sidebar.divider()
    st.sidebar.subheader("⌨ Polytonic Keyboard")
    st.sidebar.caption("Click a combo to copy for pasting into text")

    for label, combos in POLYTONIC_GROUPS:
        with st.sidebar.expander(label):
            for name, combo in combos.items():
                glyph = POLYTONIC_MAP.get(combo, "")
                if glyph:
                    if st.sidebar.button(f"{name} → {glyph}", key=f"pb_{combo}", use_container_width=True):
                        st.session_state.pb_pending = glyph
                        st.rerun()

    st.sidebar.divider()

    # Export
    st.sidebar.subheader("Export")

    export_ctx = TenantContext("desktop", "export", "desktop")

    # Collect all edited/ground-truthed text for export
    # For now, export from the document in session state
    # In a production version, this would use the ground truth pipeline result

    if st.sidebar.button("📥 Export Markdown", use_container_width=True):
        md_exporter = MarkdownExporter()
        # Use original pipeline result for export
        export_content = "\n\n".join(
            f"## Page {p.number}\n\n" + "\n".join(
                st.session_state.ground_truth_lines.get(l.line_id, l.text)
                for l in p.lines
            )
            for p in doc.pages
        )
        st.sidebar.download_button(
            "Download Markdown",
            export_content.encode("utf-8"),
            file_name=f"{Path(file_name).stem}.md",
            key="dl_md",
            use_container_width=True,
        )

    if st.sidebar.button("📥 Export Text", use_container_width=True):
        txt_content = "\n".join(
            st.session_state.ground_truth_lines.get(l.line_id, l.text)
            for p in doc.pages
            for l in p.lines
        )
        st.sidebar.download_button(
            "Download Text",
            txt_content.encode("utf-8"),
            file_name=f"{Path(file_name).stem}.txt",
            key="dl_txt",
            use_container_width=True,
        )

    # Ground truth summary — collapsible
    if st.session_state.ground_truth_lines:
        st.sidebar.divider()
        with st.sidebar.expander("Accepted Corrections"):
            for line_id, text in st.session_state.ground_truth_lines.items():
                orig = next(
                    (l.text for p in doc.pages for l in p.lines if l.line_id == line_id),
                    "",
                )
                cer = character_error_rate(orig, text)
                st.write(f"**{line_id}**: {text[:50]}…  *(CER: {cer:.3f})*")


# ---------- Main review pane ----------

if st.session_state.review_document is not None:
    doc = st.session_state.review_document
    page_idx = st.session_state.current_page
    if 0 <= page_idx < len(doc.pages):
        page = doc.pages[page_idx]

        image_col, text_col = st.columns([3, 2])

        with image_col:
            if page.image_bytes:
                img = Image.open(io.BytesIO(page.image_bytes))
                st.image(img, use_container_width=True)
            else:
                st.info("Page image not available — PyMuPDF required for page rendering")

        with text_col:
            st.subheader(f"Page {page.number}")

            if page.failures:
                for f in page.failures:
                    st.error(f"Failed: {f.message}")

            for line in page.lines:
                # Get current text (edited or ground-truthed)
                display_text = st.session_state.ground_truth_lines.get(line.line_id)
                if display_text is None:
                    display_text = st.session_state.edited_lines.get(line.line_id, line.text)

                changed = display_text != line.text

                # Confidence color bar
                conf = line.confidence
                if conf < 40:
                    conf_color = "#ff4444"
                    conf_emoji = "🔴"
                elif conf < 60:
                    conf_color = "#ffaa00"
                    conf_emoji = "🟡"
                elif conf < 80:
                    conf_color = "#44aa44"
                    conf_emoji = "🟢"
                else:
                    conf_color = "#228822"
                    conf_emoji = "✅"

                # Text display
                line_container = st.container()
                if line.is_low_confidence:
                    line_container.markdown(
                        f"""<div style="border-left:4px solid {conf_color};padding:4px 8px;margin:2px 0;background:linear-gradient(90deg,{conf_color}08,transparent)">
                        <span style="color:{conf_color};font-weight:bold">{conf_emoji} {line.confidence:.0f}%</span>
                        <span style="color:#cc0000;font-size:1.05em">{display_text}</span>
                        {("<span style='color:#888;font-size:0.85em'> (edited)</span>" if changed else "")}
                        </div>""",
                        unsafe_allow_html=True,
                    )
                else:
                    line_container.markdown(
                        f"""<div style="border-left:4px solid {conf_color};padding:4px 8px;margin:2px 0;background:linear-gradient(90deg,{conf_color}08,transparent)">
                        <span style="color:{conf_color};font-weight:bold">{conf_emoji} {line.confidence:.0f}%</span>
                        {"<span style='text-decoration:underline;text-decoration-color:#228822'>" if changed else ""}
                        {display_text}
                        {"</span>" if changed else ""}
                        {("<span style='color:#888;font-size:0.85em'> (edited)</span>" if changed else "")}
                        </div>""",
                        unsafe_allow_html=True,
                    )

                # Inline editing — text input that appears on click
                edit_key = f"edit_{line.line_id}"
                if edit_key not in st.session_state:
                    st.session_state[edit_key] = display_text

                with st.expander(f"✏️ Edit line"):
                    new_text = st.text_area(
                        "Correct this line",
                        value=display_text,
                        key=f"ta_{line.line_id}",
                        label_visibility="collapsed",
                    )
                    if new_text != display_text:
                        if line.text != new_text:
                            st.session_state.edited_lines[line.line_id] = new_text
                        else:
                            st.session_state.edited_lines.pop(line.line_id, None)
                        st.rerun()

                    if st.session_state.pb_pending:
                        st.info(f"→ Click to insert: **{st.session_state.pb_pending}**")

                # Suggestions expander
                if line.suggestions:
                    with st.expander(f"💡 {len(line.suggestions)} suggestion(s)"):
                        for s in line.suggestions:
                            accept_key = f"accept_{s.line_id}_{s.reason}"
                            if s.suggestion_text:
                                st.write(f"**{s.reason}**: {s.suggestion_text}")
                            else:
                                st.write(f"**{s.reason}**: flag only")
                            if s.reversible and s.suggestion_text:
                                if st.button("Accept", key=accept_key):
                                    st.session_state.ground_truth_lines[s.line_id] = s.suggestion_text
                                    st.rerun()
                            if st.session_state.ground_truth_lines.get(s.line_id) == s.suggestion_text:
                                st.success("Accepted")

                # Region + script metadata
                st.caption(
                    f"{line.script} · {line.region_type} · reading order #{line.reading_order}"
                )

        st.divider()

        # Ground truth toggle at bottom
        if st.session_state.ground_truth_lines:
            with st.expander(f"📝 Accepted Corrections ({len(st.session_state.ground_truth_lines)})"):
                for line_id, text in st.session_state.ground_truth_lines.items():
                    orig = next(
                        (l.text for p in doc.pages for l in p.lines if l.line_id == line_id),
                        "",
                    )
                    cer = character_error_rate(orig, text)
                    st.write(f"**{line_id}**: {text}  *(CER: {cer:.4f})*")

else:
    st.info("Upload a PDF or image to begin review")
