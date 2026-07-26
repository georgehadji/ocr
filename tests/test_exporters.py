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
    RegionType,
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
    assert text_line.attrib["regionType"] == "unknown"
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
    assert text_line.attrib["REGION_TYPE"] == "unknown"
    assert text_line.attrib["READING_ORDER"] == "0"
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


def _critical_page() -> DocumentPage:
    """Return a page with main text, apparatus, and scholia regions."""
    run = EngineRun(
        engine="tesseract",
        model_ref=ModelRef("tesseract", "ell", "hash-1"),
        model_hash="hash-1",
        params=(),
        timestamp="2026-01-01T00:00:00Z",
    )
    return DocumentPage(
        number=1,
        width=600,
        height=900,
        lines=(
            OCRLine(
                id="line-1",
                text="κεφάλαιον Α",
                confidence=Confidence(95.0),
                bbox=BBox(10, 10, 580, 30),
                script=Script.ANCIENT,
                region_type=RegionType.MAIN_TEXT,
                reading_order=1,
                provenance=run,
            ),
            OCRLine(
                id="line-2",
                text="τοῦτο τὸ κείμενον",
                confidence=Confidence(92.0),
                bbox=BBox(10, 50, 400, 25),
                script=Script.ANCIENT,
                region_type=RegionType.MAIN_TEXT,
                reading_order=2,
                provenance=run,
            ),
            OCRLine(
                id="line-3",
                text="cf. line 1 supra",
                confidence=Confidence(70.0),
                bbox=BBox(10, 600, 580, 20),
                script=Script.MODERN,
                region_type=RegionType.APPARATUS,
                reading_order=3,
                provenance=run,
            ),
            OCRLine(
                id="line-4",
                text="σχόλιον",
                confidence=Confidence(85.0),
                bbox=BBox(420, 50, 180, 30),
                script=Script.ANCIENT,
                region_type=RegionType.SCHOLIA,
                reading_order=4,
                provenance=run,
            ),
        ),
    )


def test_alto_export_region_type_and_reading_order_preserved() -> None:
    """ALTO export preserves region_type and reading_order for critical editions."""
    document = DocumentStructure(pages=(_critical_page(),))
    result = AltoXmlExporter().export(document, TenantContext("o", "u", "d"))

    assert result.is_ok()
    root = ET.fromstring(result.value)
    text_lines = list(root.iter() if root.tag.endswith("TextLine") else root.iter())

    text_lines = [
        elem
        for elem in root.iter()
        if elem.tag.endswith("TextLine") and elem.attrib.get("ID") in {"line-1", "line-2", "line-3", "line-4"}
    ]

    regions = {
        elem.attrib["ID"]: {
            "REGION_TYPE": elem.attrib.get("REGION_TYPE"),
            "READING_ORDER": elem.attrib.get("READING_ORDER"),
        }
        for elem in text_lines
    }

    assert regions["line-1"]["REGION_TYPE"] == "main"
    assert regions["line-1"]["READING_ORDER"] == "1"
    assert regions["line-3"]["REGION_TYPE"] == "apparatus"
    assert regions["line-4"]["REGION_TYPE"] == "scholia"
    assert regions["line-4"]["READING_ORDER"] == "4"


def test_page_xml_export_region_type_preserved() -> None:
    """PAGE-XML export preserves region_type for critical editions."""
    document = DocumentStructure(pages=(_critical_page(),))
    result = PageXmlExporter().export(document, TenantContext("o", "u", "d"))

    assert result.is_ok()
    root = ET.fromstring(result.value)
    text_lines = [
        elem
        for elem in root.iter()
        if elem.tag.endswith("TextLine") and elem.attrib.get("id") in {"line-1", "line-2", "line-3", "line-4"}
    ]

    regions = {
        elem.attrib["id"]: elem.attrib.get("regionType")
        for elem in text_lines
    }

    assert regions["line-1"] == "main"
    assert regions["line-3"] == "apparatus"
    assert regions["line-4"] == "scholia"
    assert len(text_lines) == 4
