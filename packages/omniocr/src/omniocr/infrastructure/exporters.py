from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET  # nosec B405 - builds/writes XML here, never parses input

from omniocr.domain.errors import ExportError
from omniocr.domain.models import DocumentStructure, ParagraphRole, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import IExporter

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from docx.text.paragraph import Paragraph


class PlainTextExporter(IExporter):
    """Export OCR text as plain text with one line per recognized segment."""

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        lines = []
        for page in document.pages:
            if page.paragraphs:
                for para in page.paragraphs:
                    lines.append(para.text)
            else:
                for line in page.lines:
                    lines.append(line.text)
        return Ok("\n".join(lines).encode("utf-8"))


class MarkdownExporter(IExporter):
    """Export source OCR text with page boundaries and no correction rewrite."""

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        sections: list[str] = []
        for page in document.pages:
            if page.paragraphs:
                para_texts = []
                for para in page.paragraphs:
                    if para.role == "heading":
                        para_texts.append(f"# {para.text}")
                    elif para.role == "subheading":
                        para_texts.append(f"## {para.text}")
                    elif para.role == "page_number":
                        para_texts.append(f"<!-- Page Number: {para.text} -->")
                    elif para.role == "running_head":
                        para_texts.append(f"<!-- Running Head: {para.text} -->")
                    elif para.role == "footnote":
                        para_texts.append(f"*[Footnote]* {para.text}")
                    else:
                        para_texts.append(para.text)
                lines = "\n\n".join(para_texts)
            else:
                lines = "\n".join(line.text for line in page.lines)
            sections.append(f"## Page {page.number}\n\n{lines}")
        return Ok(("\n\n".join(sections) + ("\n" if sections else "")).encode("utf-8"))


class AltoXmlExporter(IExporter):
    """Export positional OCR as ALTO XML with confidence and provenance metadata."""

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        try:
            root = ET.Element(
                "alto",
                {
                    "xmlns": "http://www.loc.gov/standards/alto/ns-v4#",
                    "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                },
            )
            description = ET.SubElement(root, "Description")
            ET.SubElement(
                description,
                "OCRProcessing",
                {"ID": "omniocr", "PROCESSINGDateTime": ""},
            )
            layout = ET.SubElement(root, "Layout")
            for page in document.pages:
                alto_page = ET.SubElement(
                    layout,
                    "Page",
                    {
                        "ID": f"page-{page.number}",
                        "PHYSICAL_IMG_NR": str(page.number),
                        "WIDTH": str(page.width),
                        "HEIGHT": str(page.height),
                    },
                )
                print_space = ET.SubElement(
                    alto_page,
                    "PrintSpace",
                    {
                        "HPOS": "0",
                        "VPOS": "0",
                        "WIDTH": str(page.width),
                        "HEIGHT": str(page.height),
                    },
                )
                for line in page.lines:
                    attrs = {
                        "ID": line.id,
                        "HPOS": str(line.bbox.x),
                        "VPOS": str(line.bbox.y),
                        "WIDTH": str(line.bbox.w),
                        "HEIGHT": str(line.bbox.h),
                        "WC": f"{line.confidence.value / 100:.6f}",
                        "SCRIPT": line.script.value,
                        "REGION_TYPE": line.region_type.value,
                        "READING_ORDER": str(line.reading_order),
                        # ENHANCEMENT_PLAN A5. An archival consumer should be
                        # able to see how contested a line was — engines
                        # disagreeing is better evidence than any engine's
                        # self-reported confidence. Recorded, never acted on:
                        # a SPLIT line exports its full text like any other.
                        "AGREEMENT": line.agreement.value,
                    }
                    if line.provenance is not None:
                        attrs.update(
                            {
                                "ENGINE": line.provenance.engine,
                                "MODEL": line.provenance.model_ref.model_name,
                                "MODEL_HASH": line.provenance.model_hash,
                            }
                        )
                    text_line = ET.SubElement(print_space, "TextLine", attrs)
                    ET.SubElement(
                        text_line,
                        "String",
                        {
                            "ID": f"{line.id}-text",
                            "CONTENT": line.text,
                            "WC": f"{line.confidence.value / 100:.6f}",
                        },
                    )
            return Ok(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        except (TypeError, ValueError, ET.ParseError) as exc:
            return Err(ExportError(f"ALTO export failed: {exc}"))


class PageXmlExporter(IExporter):
    """Export positional OCR as PAGE-XML for archival and training workflows."""

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        try:
            root = ET.Element(
                "PcGts",
                {"xmlns": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"},
            )
            for page in document.pages:
                page_element = ET.SubElement(
                    root,
                    "Page",
                    {
                        "imageWidth": str(page.width),
                        "imageHeight": str(page.height),
                        "imageFilename": f"page-{page.number}",
                    },
                )
                text_region = ET.SubElement(
                    page_element, "TextRegion", {"id": f"region-{page.number}"}
                )
                for line in page.lines:
                    points = (
                        f"{line.bbox.x},{line.bbox.y} "
                        f"{line.bbox.right},{line.bbox.y} "
                        f"{line.bbox.right},{line.bbox.bottom} "
                        f"{line.bbox.x},{line.bbox.bottom}"
                    )
                    text_line = ET.SubElement(
                        text_region,
                        "TextLine",
                        {
                            "id": line.id,
                            "regionType": line.region_type.value,
                            "agreement": line.agreement.value,
                        },
                    )
                    ET.SubElement(text_line, "Coords", {"points": points})
                    if line.provenance is not None:
                        user_defined = ET.SubElement(text_line, "UserDefined")
                        ET.SubElement(
                            user_defined,
                            "UserAttribute",
                            {"name": "engine", "value": line.provenance.engine},
                        )
                        ET.SubElement(
                            user_defined,
                            "UserAttribute",
                            {
                                "name": "model",
                                "value": line.provenance.model_ref.model_name,
                            },
                        )
                        ET.SubElement(
                            user_defined,
                            "UserAttribute",
                            {"name": "modelHash", "value": line.provenance.model_hash},
                        )
                    unicode_element = ET.SubElement(text_line, "TextEquiv")
                    ET.SubElement(
                        unicode_element,
                        "Unicode",
                        {
                            "conf": f"{line.confidence.value / 100:.6f}",
                            "script": line.script.value,
                        },
                    ).text = line.text
            return Ok(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        except (TypeError, ValueError, ET.ParseError) as exc:
            return Err(ExportError(f"PAGE-XML export failed: {exc}"))


class SearchablePdfExporter(IExporter):
    """Overlay invisible OCR text on the original PDF page images.

    When ``font_path`` is ``None`` and the first Greek line is encountered, the
    exporter tries to auto-detect a polytonic-capable system font from common
    OS font directories. If none is found, it returns an ``Err`` with a clear
    message telling the caller which fonts were attempted.
    """

    _FONT_CANDIDATES: tuple[str, ...] = (
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/arial.ttf",
        "/Library/Fonts/Arial.ttf",
    )

    def __init__(self, source_pdf: bytes, font_path: str | Path | None = None) -> None:
        self._source_pdf = source_pdf
        self._font_path = Path(font_path) if font_path is not None else None
        self._resolved_font: str | None = None

    @staticmethod
    def _find_system_font() -> str | None:
        """Walk known font paths for a file with Greek glyph coverage."""
        for candidate in SearchablePdfExporter._FONT_CANDIDATES:
            path = Path(candidate)
            if path.is_file():
                return str(path.resolve())
        return None

    def _resolve_font(self) -> str | None:
        """Return the configured or auto-detected font path."""
        if self._font_path is not None:
            return str(self._font_path.resolve())
        if self._resolved_font is None:
            self._resolved_font = self._find_system_font()
        return self._resolved_font

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        try:
            import fitz

            pdf = fitz.open(stream=self._source_pdf, filetype="pdf")
            try:
                if len(pdf) < len(document.pages):
                    return Err(ExportError("OCR document has more pages than the source PDF"))
                font_name = "helv"
                unicode_font = self._resolve_font()
                for index, ocr_page in enumerate(document.pages):
                    page = pdf[index]
                    x_scale = page.rect.width / max(1, ocr_page.width)
                    y_scale = page.rect.height / max(1, ocr_page.height)
                    for line in ocr_page.lines:
                        if not line.text:
                            continue
                        page_font = font_name
                        if any(ord(character) > 127 for character in line.text):
                            if unicode_font is None:
                                return Err(
                                    ExportError(
                                        "Unicode OCR text requires a font_path with Greek "
                                        "glyph coverage — none found on system paths"
                                    )
                                )
                            if page_font == "helv":
                                page.insert_font(fontname="omniocr-unicode", fontfile=unicode_font)
                                page_font = "omniocr-unicode"
                        rect = fitz.Rect(
                            line.bbox.x * x_scale,
                            line.bbox.y * y_scale,
                            line.bbox.right * x_scale,
                            line.bbox.bottom * y_scale,
                        )
                        fontsize = max(4.0, min(12.0, rect.height * 0.6))
                        inserted = page.insert_text(
                            (rect.x0, rect.y1 - max(2.0, fontsize * 0.2)),
                            line.text,
                            fontsize=fontsize,
                            fontname=page_font,
                            render_mode=3,
                            overlay=True,
                        )
                        if inserted <= 0:
                            return Err(ExportError(f"could not place OCR line {line.id}"))
                result_bytes = pdf.tobytes(garbage=3, deflate=True)
                return Ok(result_bytes)
            finally:
                pdf.close()
                # Release the source PDF bytes to free memory after export.
                self._source_pdf = b""
        except Exception as exc:
            self._source_pdf = b""
            return Err(ExportError(f"searchable PDF export failed: {exc}"))


_STYLE_BY_ROLE: dict[ParagraphRole, str] = {
    ParagraphRole.HEADING: "Heading 1",
    ParagraphRole.SUBHEADING: "Heading 2",
    ParagraphRole.FOOTNOTE: "Footnote Text",
    ParagraphRole.RUNNING_HEAD: "Header",
    ParagraphRole.BODY: "Normal",
}


def _resolve_paragraph_style(doc: "DocxDocument", name: str) -> str:
    """Return ``name`` if the template has it; else synthesize it from Normal.

    python-docx's default template ships only a handful of built-in styles
    (Heading 1/2, Header, Normal) — "Footnote Text" is a real Word style but
    isn't pre-registered. Silently falling back to Normal here would defeat
    the point of role-aware styling, so an unknown-but-requested style is
    created instead of discarded.
    """
    try:
        doc.styles[name]
        return name
    except KeyError:
        pass
    if name == "Normal":
        return "Normal"
    from docx.enum.style import WD_STYLE_TYPE

    style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["Normal"]
    if name == "Footnote Text":
        style.font.italic = True
    return name


def _add_styled_run(paragraph: "Paragraph", text: str, font_name: str) -> None:
    from docx.oxml.ns import qn

    run = paragraph.add_run(text)
    run.font.name = font_name
    run_rpr = run._element.get_or_add_rPr()
    run_rpr.get_or_add_rFonts().set(qn("w:eastAsia"), font_name)


class DocxExporter(IExporter):
    """Export OCR text to DOCX without changing the recognized source text."""

    def __init__(self, font_name: str = "Cambria") -> None:
        self._font_name = font_name

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        try:
            from io import BytesIO

            from docx import Document
            from docx.oxml.ns import qn

            output = BytesIO()
            doc = Document()
            style = doc.styles["Normal"]
            style.font.name = self._font_name
            # `.rPr`/`.rFonts` are both None until a run/style has any
            # formatting applied — a freshly styled document can hit that, so
            # this would have been a real AttributeError at runtime, not just
            # an unannotated type.
            rpr = style._element.get_or_add_rPr()
            rpr.get_or_add_rFonts().set(qn("w:eastAsia"), self._font_name)
            for page_index, page in enumerate(document.pages):
                if page_index:
                    doc.add_page_break()

                if page.paragraphs:
                    for para in page.paragraphs:
                        style_name = _resolve_paragraph_style(
                            doc, _STYLE_BY_ROLE.get(para.role, "Normal")
                        )
                        paragraph = doc.add_paragraph(style=style_name)
                        _add_styled_run(paragraph, para.text, self._font_name)
                else:
                    for line in page.lines:
                        paragraph = doc.add_paragraph()
                        _add_styled_run(paragraph, line.text, self._font_name)
            doc.save(output)
            return Ok(output.getvalue())
        except ImportError:
            return Err(ExportError("DOCX export requires the optional 'docx' dependency"))
        except (AttributeError, OSError, ValueError) as exc:
            return Err(ExportError(f"DOCX export failed: {exc}"))


__all__ = [
    "AltoXmlExporter",
    "DocxExporter",
    "MarkdownExporter",
    "PageXmlExporter",
    "PlainTextExporter",
    "SearchablePdfExporter",
]
