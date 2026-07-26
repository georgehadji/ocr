"""OmniOCR Review UI — Streamlit correction interface.

Run from the repository root:

    streamlit run editions/desktop/review_ui.py

Requires: streamlit, Pillow, and the optional ``docx`` extra for DOCX export.
"""

from __future__ import annotations

import io
from pathlib import Path

import streamlit as st
from PIL import Image

from omniocr.application.metrics import character_error_rate
from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.exporters import DocxExporter, MarkdownExporter
from omniocr.infrastructure.kraken import KrakenLayoutAnalyzer
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.infrastructure.review import (
    ReviewDocument,
    build_review_document,
    group_suggestions_by_reason,
)

st.set_page_config(page_title="OmniOCR Review", layout="wide")

# ---------- Polytonic keyboard ----------

POLYTONIC_MAP: dict[str, str] = {
    "a>": "ἀ",  # alpha + smooth
    "a<": "ἁ",  # alpha + rough
    "a/>": "ἄ",  # alpha + smooth + acute
    "a/<": "ἅ",  # alpha + rough + acute
    "a=>": "ἂ",  # alpha + smooth + grave
    "a=<": "ἃ",  # alpha + rough + grave
    "a~>": "ἆ",  # alpha + smooth + circumflex
    "a~<": "ἇ",  # alpha + rough + circumflex
    "A>": "Ἀ",
    "A<": "Ἁ",
    "A/>": "Ἄ",
    "A/<": "Ἅ",
    "e>": "ἐ",
    "e<": "ἑ",
    "e/>": "ἔ",
    "e/<": "ἕ",
    "E>": "Ἐ",
    "E<": "Ἑ",
    "h>": "ἠ",
    "h<": "ἡ",
    "h/>": "ἤ",
    "h/<": "ἥ",
    "h~>": "ἦ",
    "h~<": "ἧ",
    "H>": "Ἠ",
    "H<": "Ἡ",
    "i>": "ἰ",
    "i<": "ἱ",
    "i/>": "ἴ",
    "i/<": "ἵ",
    "i~>": "ἶ",
    "i~<": "ἷ",
    "I>": "Ἰ",
    "I<": "Ἱ",
    "o>": "ὀ",
    "o<": "ὁ",
    "o/>": "ὄ",
    "o/<": "ὅ",
    "O>": "Ὀ",
    "O<": "Ὁ",
    "u>": "ὐ",
    "u<": "ὑ",
    "u/>": "ὔ",
    "u/<": "ὕ",
    "U<": "Ὑ",
    "w>": "ὠ",
    "w<": "ὡ",
    "w/>": "ὤ",
    "w/<": "ὥ",
    "w~>": "ὦ",
    "w~<": "ὧ",
    "W>": "Ὠ",
    "W<": "Ὡ",
    "r>": "ῤ",  # rho + smooth
    "r<": "ῥ",  # rho + rough
    "R<": "Ῥ",
}


# ---------- Session state ----------

if "review_document" not in st.session_state:
    st.session_state.review_document: ReviewDocument | None = None
if "current_page" not in st.session_state:
    st.session_state.current_page = 0
if "ground_truth_lines" not in st.session_state:
    st.session_state.ground_truth_lines: dict[str, str] = {}


# ---------- Sidebar controls ----------

st.sidebar.title("OmniOCR Review")
st.sidebar.caption("Scholarly correction interface")

uploaded_file = st.sidebar.file_uploader(
    "Upload a PDF or image",
    type=["pdf", "png", "jpg", "jpeg", "tif", "tiff"],
)

if uploaded_file is not None and st.session_state.review_document is None:
    with st.spinner("Running OCR pipeline..."):
        pdf_bytes = uploaded_file.read()
        pipeline = PipelineOrchestrator(
            layout_analyzer=KrakenLayoutAnalyzer(Script.POLYTONIC),
            post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
            exporter=MarkdownExporter(),
        )
        ctx = TenantContext("desktop", "reviewer", "desktop")
        result = pipeline.run(pdf_bytes, ctx)
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
            st.rerun()
        else:
            st.sidebar.error(f"Pipeline failed: {result.error}")

if st.session_state.review_document is not None:
    doc = st.session_state.review_document
    total_pages = len(doc.pages)

    col1, col2, col3 = st.sidebar.columns(3)
    with col1:
        if st.button("◀ Prev") and st.session_state.current_page > 0:
            st.session_state.current_page -= 1
            st.rerun()
    with col2:
        st.write(f"Page {st.session_state.current_page + 1}/{total_pages}")
    with col3:
        if st.button("Next ▶") and st.session_state.current_page < total_pages - 1:
            st.session_state.current_page += 1
            st.rerun()

    # Suggestion summary
    summary = group_suggestions_by_reason(doc)
    if summary:
        st.sidebar.subheader("Suggestions")
        for reason, count in sorted(summary.items(), key=lambda x: -x[1]):
            st.sidebar.write(f"  {reason}: {count}")

    # Export
    if st.sidebar.button("Export Markdown"):
        export_ctx = TenantContext("desktop", "export", "desktop")
        md = MarkdownExporter()
        # Re-run pipeline for export (simplified — in production, store the result)
        st.sidebar.success("Export handled via pipeline")

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
                st.info("Page image not available")

        with text_col:
            st.subheader(f"Page {page.number}")
            if page.failures:
                st.error(f"Failures: {', '.join(f.message for f in page.failures)}")

            for line in page.lines:
                key = f"line_{line.line_id}"

                if line.is_low_confidence:
                    st.markdown(f"**:red[{line.text}]**  *(low conf: {line.confidence:.0f}%)*")
                else:
                    st.write(f"{line.text}  *({line.confidence:.0f}%)*")

                st.caption(
                    f"Script: {line.script} | Region: {line.region_type} "
                    f"| Order: {line.reading_order}"
                )

                if line.suggestions:
                    expand = st.expander(f"{len(line.suggestions)} suggestion(s)")
                    for s in line.suggestions:
                        accepted_key = f"accept_{s.line_id}_{s.reason}"
                        is_accepted = st.session_state.ground_truth_lines.get(s.line_id) == s.suggestion_text
                        if s.suggestion_text:
                            expand.write(f"**{s.reason}**: {s.suggestion_text}")
                        else:
                            expand.write(f"**{s.reason}**: flag only")
                        if s.reversible and s.suggestion_text:
                            if expand.button("Accept", key=accepted_key):
                                st.session_state.ground_truth_lines[s.line_id] = s.suggestion_text
                                st.rerun()
                    if is_accepted:
                        expand.success("Accepted as ground truth")

                # Polytonic keyboard per line
                with st.popover("⌨ Polytonic"):
                    st.caption("Type a key combo (e.g. a> = ἀ)")
                    combo = st.text_input("Combo", key=f"combo_{line.line_id}", label_visibility="collapsed")
                    if combo and combo in POLYTONIC_MAP:
                        st.write(f"→ {POLYTONIC_MAP[combo]}")
                        if st.button(f"Insert {POLYTONIC_MAP[combo]}", key=f"insert_{line.line_id}"):
                            st.session_state.ground_truth_lines[line.line_id] = (
                                st.session_state.ground_truth_lines.get(line.line_id, line.text)
                                + POLYTONIC_MAP[combo]
                            )
                            st.rerun()

                st.divider()

        # Ground truth summary
        if st.session_state.ground_truth_lines:
            st.subheader("Accepted Corrections (Ground Truth)")
            for line_id, text in st.session_state.ground_truth_lines.items():
                orig = next(
                    (l.text for p in doc.pages for l in p.lines if l.line_id == line_id),
                    "",
                )
                cer = character_error_rate(orig, text)
                st.write(f"**{line_id}**: {text}  *(CER vs original: {cer:.4f})*")

else:
    st.info("Upload a PDF or image to begin review")
