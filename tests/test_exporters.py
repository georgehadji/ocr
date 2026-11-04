from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    EngineRun,
    ModelRef,
    OCRLine,
    OCRParagraph,
    ParagraphRole,
    RegionType,
    Script,
    TenantContext,
)
from omniocr.infrastructure.exporters import (
    AltoXmlExporter,
    DocxExporter,
    MarkdownExporter,
    PageXmlExporter,
    PlainTextExporter,
)
from omniocr.infrastructure.exporters import SearchablePdfExporter


def test_docx_exporter_writes_a_package_with_requested_font() -> None:
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


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_searchable_pdf_text_lands_inside_its_box_on_rotated_pages(rotation: int) -> None:
    """OCR boxes are in the visible page's space, whatever the page's /Rotate."""
    fitz = pytest.importorskip("fitz")
    source = fitz.open()
    source.new_page(width=400, height=600).set_rotation(rotation)
    source_pdf = source.tobytes()
    source.close()
    visible_width, visible_height = (400, 600) if rotation in (0, 180) else (600, 400)
    box = BBox(100, 100, 200, 30)

    result = SearchablePdfExporter(source_pdf).export(
        DocumentStructure(
            (
                DocumentPage(
                    1,
                    visible_width,
                    visible_height,
                    (OCRLine("line-1", "HELLO", Confidence(90), box),),
                ),
            )
        ),
        TenantContext("o", "u", "desktop"),
    )

    assert result.is_ok()
    output = fitz.open(stream=result.value, filetype="pdf")
    page = output[0]
    [word] = [w for w in page.get_text("words") if w[4] == "HELLO"]
    # get_text reports the unrotated page; map it back to what a reader sees.
    seen = fitz.Rect(word[:4]) * page.rotation_matrix
    expected = fitz.Rect(box.x - 3, box.y - 3, box.right + 3, box.bottom + 3)
    output.close()
    assert expected.contains(seen), f"{seen} not inside {expected}"


def _pdf_bytes() -> bytes:
    fitz = pytest.importorskip("fitz")
    source = fitz.open()
    source.new_page(width=100, height=100)
    data = source.tobytes()
    source.close()
    return data


def test_searchable_pdf_create_rejects_a_source_that_is_not_a_pdf() -> None:
    result = SearchablePdfExporter.create(b"\x89PNG\r\n\x1a\n not a pdf")

    assert not result.is_ok()
    assert "not a PDF" in str(result.error)


def test_searchable_pdf_create_rejects_an_unreadable_font(tmp_path) -> None:
    result = SearchablePdfExporter.create(_pdf_bytes(), font_path=tmp_path / "missing.ttf")

    assert not result.is_ok()
    assert "cannot read font" in str(result.error)


def test_searchable_pdf_create_accepts_a_pdf_with_a_greek_font() -> None:
    if SearchablePdfExporter._find_system_font() is None:
        pytest.skip("no Greek-capable system font on this machine")

    assert SearchablePdfExporter.create(_pdf_bytes()).is_ok()


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
        if elem.tag.endswith("TextLine")
        and elem.attrib.get("ID") in {"line-1", "line-2", "line-3", "line-4"}
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
        if elem.tag.endswith("TextLine")
        and elem.attrib.get("id") in {"line-1", "line-2", "line-3", "line-4"}
    ]

    regions = {elem.attrib["id"]: elem.attrib.get("regionType") for elem in text_lines}

    assert regions["line-1"] == "main"
    assert regions["line-3"] == "apparatus"
    assert regions["line-4"] == "scholia"
    assert len(text_lines) == 4


def test_paragraph_aware_exports() -> None:
    """Verify that PlainText, Markdown, and Docx exporters handle paragraphs and roles correctly."""
    run = EngineRun(
        engine="tesseract",
        model_ref=ModelRef("tesseract", "ell", "hash-1"),
        model_hash="hash-1",
        params=(),
        timestamp="2026-01-01T00:00:00Z",
    )
    line1 = OCRLine(
        id="line-1",
        text="This is a heading",
        confidence=Confidence(90.0),
        bbox=BBox(10, 10, 200, 30),
        script=Script.MODERN,
        provenance=run,
    )
    line2 = OCRLine(
        id="line-2",
        text="This is body text.",
        confidence=Confidence(95.0),
        bbox=BBox(10, 50, 400, 25),
        script=Script.MODERN,
        provenance=run,
    )
    line3 = OCRLine(
        id="line-3",
        text="A footnote text.",
        confidence=Confidence(80.0),
        bbox=BBox(10, 500, 300, 20),
        script=Script.MODERN,
        provenance=run,
    )

    para_heading = OCRParagraph(
        id="p-1",
        lines=(line1,),
        text="This is a heading",
        role=ParagraphRole.HEADING,
    )
    para_body = OCRParagraph(
        id="p-2",
        lines=(line2,),
        text="This is body text.",
        role=ParagraphRole.BODY,
    )
    para_footnote = OCRParagraph(
        id="p-3",
        lines=(line3,),
        text="A footnote text.",
        role=ParagraphRole.FOOTNOTE,
    )

    doc_struct = DocumentStructure(
        pages=(
            DocumentPage(
                number=1,
                width=600,
                height=800,
                lines=(line1, line2, line3),
                paragraphs=(para_heading, para_body, para_footnote),
            ),
        )
    )

    ctx = TenantContext("o", "u", "desktop")

    # 1. PlainTextExporter
    pt_result = PlainTextExporter().export(doc_struct, ctx)
    assert pt_result.is_ok()
    pt_text = pt_result.value.decode("utf-8")
    assert pt_text == "This is a heading\nThis is body text.\nA footnote text."

    # 2. MarkdownExporter
    md_result = MarkdownExporter().export(doc_struct, ctx)
    assert md_result.is_ok()
    md_text = md_result.value.decode("utf-8")
    expected_md = (
        "## Page 1\n\n# This is a heading\n\nThis is body text.\n\n*[Footnote]* A footnote text.\n"
    )
    assert md_text == expected_md

    # 3. DocxExporter
    pytest.importorskip("docx")
    from io import BytesIO

    from docx import Document

    docx_result = DocxExporter().export(doc_struct, ctx)
    assert docx_result.is_ok()
    written = Document(BytesIO(docx_result.value))
    styles = [p.style.name for p in written.paragraphs]
    texts = [p.text for p in written.paragraphs]
    assert styles == ["Heading 1", "Normal", "Footnote Text"]
    assert texts == ["This is a heading", "This is body text.", "A footnote text."]
