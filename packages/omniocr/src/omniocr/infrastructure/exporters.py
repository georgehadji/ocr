from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from omniocr.domain.errors import ExportError
from omniocr.domain.models import DocumentStructure, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import IExporter


class MarkdownExporter(IExporter):
    """Export source OCR text with page boundaries and no correction rewrite."""

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        sections: list[str] = []
        for page in document.pages:
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


class SearchablePdfExporter(IExporter):
    """Overlay invisible OCR text on the original PDF page images."""

    def __init__(self, source_pdf: bytes, font_path: str | Path | None = None) -> None:
        self._source_pdf = source_pdf
        self._font_path = Path(font_path) if font_path is not None else None

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
                if self._font_path is not None:
                    if not self._font_path.is_file():
                        return Err(ExportError(f"Unicode PDF font not found: {self._font_path}"))
                    font_name = "omniocr-unicode"
                for index, ocr_page in enumerate(document.pages):
                    page = pdf[index]
                    x_scale = page.rect.width / max(1, ocr_page.width)
                    y_scale = page.rect.height / max(1, ocr_page.height)
                    for line in ocr_page.lines:
                        if not line.text:
                            continue
                        if any(ord(character) > 127 for character in line.text):
                            if self._font_path is None:
                                return Err(
                                    ExportError(
                                        "Unicode OCR text requires a font_path with Greek glyph coverage"
                                    )
                                )
                            page.insert_font(fontname=font_name, fontfile=str(self._font_path))
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
                            fontname=font_name,
                            render_mode=3,
                            overlay=True,
                        )
                        if inserted <= 0:
                            return Err(ExportError(f"could not place OCR line {line.id}"))
                return Ok(pdf.tobytes(garbage=3, deflate=True))
            finally:
                pdf.close()
        except Exception as exc:
            return Err(ExportError(f"searchable PDF export failed: {exc}"))


class DocxExporter(IExporter):
    """Export OCR text to DOCX without changing the recognized source text."""

    def __init__(self, font_name: str = "Gentium Plus") -> None:
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
            style._element.rPr.rFonts.set(qn("w:eastAsia"), self._font_name)
            for page_index, page in enumerate(document.pages):
                if page_index:
                    doc.add_page_break()
                for line in page.lines:
                    paragraph = doc.add_paragraph()
                    run = paragraph.add_run(line.text)
                    run.font.name = self._font_name
                    run._element.rPr.rFonts.set(qn("w:eastAsia"), self._font_name)
            doc.save(output)
            return Ok(output.getvalue())
        except ImportError:
            return Err(ExportError("DOCX export requires the optional 'docx' dependency"))
        except (AttributeError, OSError, ValueError) as exc:
            return Err(ExportError(f"DOCX export failed: {exc}"))


__all__ = ["AltoXmlExporter", "DocxExporter", "MarkdownExporter", "SearchablePdfExporter"]
