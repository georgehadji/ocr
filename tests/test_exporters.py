from __future__ import annotations

from xml.etree import ElementTree as ET

from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    EngineRun,
    ModelRef,
    OCRLine,
    Script,
    TenantContext,
)
from omniocr.infrastructure.exporters import (
    AltoXmlExporter,
    DocxExporter,
    MarkdownExporter,
    PageXmlExporter,
)
from omniocr.infrastructure.exporters import SearchablePdfExporter


def test_docx_exporter_writes_a_package_with_requested_font() -> None:
    pytest = __import__("pytest")
    pytest.importorskip("docx")

    result = DocxExporter("New Athena Unicode").export(
        _document(), TenantContext("o", "u", "desktop")
    )

    assert result.is_ok()
    assert result.value.startswith(b"PK")


def test_page_xml_export_contains_coordinates_text_and_confidence() -> None:
    result = PageXmlExporter().export(_document(), TenantContext("o", "u", "desktop"))

    assert result.is_ok()
    root = ET.fromstring(result.value)
    text_line = next(element for element in root.iter() if element.tag.endswith("TextLine"))
    coords = next(element for element in root.iter() if element.tag.endswith("Coords"))
    unicode_element = next(element for element in root.iter() if element.tag.endswith("Unicode"))
    user_attributes = [
        element.attrib["value"] for element in root.iter() if element.tag.endswith("UserAttribute")
    ]
    assert text_line.attrib["id"] == "line-1"
    assert coords.attrib["points"] == "10,20 310,20 310,60 10,60"
    assert unicode_element.attrib["conf"] == "0.875000"
    assert unicode_element.text is not None
    assert user_attributes == ["tesseract", "ell", "hash-1"]


def _document() -> DocumentStructure:
    run = EngineRun(
        engine="tesseract",
        model_ref=ModelRef("tesseract", "ell", "hash-1"),
        model_hash="hash-1",
        params=(),
        timestamp="2026-01-01T00:00:00Z",
    )
    line = OCRLine(
        id="line-1",
        text="Ἑλληνικά & λόγος",
        confidence=Confidence(87.5),
        bbox=BBox(10, 20, 300, 40),
        script=Script.POLYTONIC,
        provenance=run,
    )
    return DocumentStructure((DocumentPage(1, 600, 800, (line,)),))


def test_markdown_export_preserves_unicode_and_page_heading() -> None:
    result = MarkdownExporter().export(_document(), TenantContext("o", "u", "desktop"))

    assert result.is_ok()
    assert result.value.decode("utf-8") == "## Page 1\n\nἙλληνικά & λόγος\n"


def test_alto_export_contains_geometry_confidence_and_provenance() -> None:
    result = AltoXmlExporter().export(_document(), TenantContext("o", "u", "desktop"))

    assert result.is_ok()
    root = ET.fromstring(result.value)
    page = next(element for element in root.iter() if element.tag.endswith("Page"))
    text_line = next(element for element in root.iter() if element.tag.endswith("TextLine"))
    string = next(element for element in root.iter() if element.tag.endswith("String"))
    assert page.attrib["WIDTH"] == "600"
    assert text_line.attrib["HPOS"] == "10"
    assert text_line.attrib["WC"] == "0.875000"
    assert text_line.attrib["ENGINE"] == "tesseract"
    assert string.attrib["CONTENT"] == "Ἑλληνικά & λόγος"


def test_searchable_pdf_export_adds_text_layer_to_original_pdf() -> None:
    fitz = __import__("pytest").importorskip("fitz")
    source = fitz.open()
    source.new_page(width=100, height=100)
    source_pdf = source.tobytes()
    source.close()

    result = SearchablePdfExporter(source_pdf).export(
        DocumentStructure(
            (
                DocumentPage(
                    1,
                    100,
                    100,
                    (
                        OCRLine(
                            "line-1",
                            "searchable text",
                            Confidence(90),
                            BBox(10, 10, 80, 20),
                        ),
                    ),
                ),
            )
        ),
        TenantContext("o", "u", "desktop"),
    )

    assert result.is_ok()
    output = fitz.open(stream=result.value, filetype="pdf")
    assert "searchable text" in output[0].get_text()
    output.close()


def test_searchable_pdf_auto_detects_or_requires_unicode_font() -> None:
    """Auto-detect a system font for Greek; fail only when none is available."""
    fitz = __import__("pytest").importorskip("fitz")
    source = fitz.open()
    source.new_page(width=100, height=100)
    source_pdf = source.tobytes()
    source.close()

    result = SearchablePdfExporter(source_pdf).export(
        DocumentStructure(
            (
                DocumentPage(
                    1,
                    100,
                    100,
                    (OCRLine("line-1", "ἄνθρωπος", Confidence(90), BBox(10, 10, 80, 20)),),
                ),
            )
        ),
        TenantContext("o", "u", "desktop"),
    )

    if result.is_ok():
        output = fitz.open(stream=result.value, filetype="pdf")
        assert "ἄνθρωπος" in output[0].get_text()
        output.close()
    else:
        assert "font_path" in str(result.error)
