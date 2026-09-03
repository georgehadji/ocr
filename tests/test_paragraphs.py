from __future__ import annotations


from omniocr.application.structure.breaks import GapRule, IndentRule, ShortLineRule
from omniocr.application.structure.geometry import (
    PageGeometry,
    column_left_edge,
    column_right_edge,
    is_centred,
    is_probably_multi_column,
    median_leading,
    median_line_height,
)
from omniocr.application.structure.assembler import DocumentAssembler
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    TenantContext,
)
from omniocr.domain.result import Ok


def test_geometry_helpers() -> None:
    conf = Confidence(100.0)
    lines = (
        OCRLine(id="l1", text="line 1", confidence=conf, bbox=BBox(10, 20, 100, 15)),
        OCRLine(id="l2", text="line 2", confidence=conf, bbox=BBox(10, 45, 105, 15)),
        OCRLine(id="l3", text="line 3", confidence=conf, bbox=BBox(12, 70, 98, 15)),
    )

    assert column_left_edge(lines) == 10
    assert (
        column_right_edge(lines) == 110
    )  # 10 + 100 = 110, 10 + 105 = 115, 12 + 98 = 110 -> Mode of right edges is 110
    assert median_line_height(lines) == 15.0
    # Gaps are: l2.y - l1.bottom = 45 - 35 = 10; l3.y - l2.bottom = 70 - 60 = 10
    assert median_leading(lines) == 10.0


def test_is_centred() -> None:
    line_centred = OCRLine(
        id="l1", text="centred", confidence=Confidence(1.0), bbox=BBox(40, 20, 40, 15)
    )
    line_not_centred = OCRLine(
        id="l2", text="left aligned", confidence=Confidence(1.0), bbox=BBox(10, 45, 40, 15)
    )

    # Column bounds: left=10, right=110 (width=100)
    # Centred line left indent: 40 - 10 = 30; right indent: 110 - 80 = 30 -> Exactly centered!
    assert is_centred(line_centred, 10, 110, tolerance=5.0) is True
    # Left aligned: left indent: 10 - 10 = 0; right indent: 110 - 50 = 60 -> Not centered!
    assert is_centred(line_not_centred, 10, 110, tolerance=5.0) is False


def test_paragraph_break_rules() -> None:
    page = PageGeometry(
        left=10,
        right=110,
        median_leading=10.0,
        median_line_height=15.0,
        width=150,
        height=200,
    )
    conf = Confidence(1.0)

    # 1. IndentRule
    # Normal line
    l1 = OCRLine(id="l1", text="normal line", confidence=conf, bbox=BBox(10, 20, 100, 15))
    # Indented line (indent = 15, which is > 10 + 0.02 * 100 = 12)
    l2 = OCRLine(id="l2", text="indented start", confidence=conf, bbox=BBox(15, 45, 95, 15))

    indent_rule = IndentRule()
    assert indent_rule.breaks_before(l1, l2, page) is True
    assert indent_rule.breaks_before(l1, l1, page) is False

    # 2. ShortLineRule
    # Short line (ends at 80, column right is 110, threshold is 110 - 15 = 95)
    l_short = OCRLine(id="l_short", text="short", confidence=conf, bbox=BBox(10, 20, 70, 15))
    # Full line
    l_full = OCRLine(id="l_full", text="full", confidence=conf, bbox=BBox(10, 20, 100, 15))

    short_rule = ShortLineRule()
    assert short_rule.breaks_before(l_short, l1, page) is True
    assert short_rule.breaks_before(l_full, l1, page) is False

    # 3. GapRule
    # Normal gap (10.0)
    l_normal = OCRLine(
        id="l_normal", text="normal gap", confidence=conf, bbox=BBox(10, 45, 100, 15)
    )
    # Large gap (gap = 75 - 35 = 40, which is > 1.5 * 10 = 15)
    l_large = OCRLine(id="l_large", text="large gap", confidence=conf, bbox=BBox(10, 75, 100, 15))

    gap_rule = GapRule()
    assert gap_rule.breaks_before(l1, l_large, page) is True
    assert gap_rule.breaks_before(l1, l_normal, page) is False


def test_document_assembler_groups_paragraphs() -> None:
    # Arrange: Build a document with 3 lines. l1 is short, indicating l2 starts a new paragraph.
    conf = Confidence(1.0)
    lines = (
        OCRLine(id="l1", text="This is a short line.", confidence=conf, bbox=BBox(10, 20, 50, 15)),
        OCRLine(
            id="l2",
            text="This starts the second paragraph",
            confidence=conf,
            bbox=BBox(10, 45, 100, 15),
        ),
        OCRLine(
            id="l3",
            text="and this is the continuation.",
            confidence=conf,
            bbox=BBox(10, 70, 100, 15),
        ),
    )
    doc = DocumentStructure(pages=(DocumentPage(number=1, width=150, height=200, lines=lines),))
    context = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")

    assembler = DocumentAssembler()

    # Act
    res = assembler.assemble(doc, context)

    # Assert
    assert isinstance(res, Ok)
    assembled_doc = res.value
    page = assembled_doc.pages[0]

    # There should be exactly 2 paragraphs
    assert len(page.paragraphs) == 2
    p1 = page.paragraphs[0]
    p2 = page.paragraphs[1]

    assert p1.text == "This is a short line."
    assert p1.lines == (lines[0],)

    assert p2.text == "This starts the second paragraph and this is the continuation."
    assert p2.lines == (lines[1], lines[2])


def test_is_probably_multi_column_detects_two_clusters() -> None:
    conf = Confidence(1.0)
    left_col = [
        OCRLine(id=f"l{i}", text=f"left {i}", confidence=conf, bbox=BBox(10, i * 25, 100, 15))
        for i in range(4)
    ]
    right_col = [
        OCRLine(id=f"r{i}", text=f"right {i}", confidence=conf, bbox=BBox(310, i * 25, 100, 15))
        for i in range(4)
    ]
    assert is_probably_multi_column(left_col + right_col, page_width=600) is True


def test_is_probably_multi_column_ignores_single_column_indentation() -> None:
    conf = Confidence(1.0)
    lines = [
        OCRLine(id="l1", text="normal", confidence=conf, bbox=BBox(10, 0, 100, 15)),
        OCRLine(id="l2", text="normal", confidence=conf, bbox=BBox(10, 25, 100, 15)),
        OCRLine(id="l3", text="indented start", confidence=conf, bbox=BBox(15, 50, 95, 15)),
        OCRLine(id="l4", text="normal", confidence=conf, bbox=BBox(10, 75, 100, 15)),
    ]
    assert is_probably_multi_column(lines, page_width=600) is False


def test_is_probably_multi_column_ignores_a_lone_marginal_note() -> None:
    conf = Confidence(1.0)
    body = [
        OCRLine(id=f"l{i}", text="body", confidence=conf, bbox=BBox(10, i * 25, 100, 15))
        for i in range(5)
    ]
    margin_note = OCRLine(id="m1", text="note", confidence=conf, bbox=BBox(500, 30, 40, 15))
    assert is_probably_multi_column(body + [margin_note], page_width=600) is False


def test_document_assembler_skips_multi_column_pages() -> None:
    """A detected two-column page is left as unassembled lines, not force-joined."""
    conf = Confidence(1.0)
    left_col = [
        OCRLine(id=f"l{i}", text=f"left {i}", confidence=conf, bbox=BBox(10, i * 25, 100, 15))
        for i in range(4)
    ]
    right_col = [
        OCRLine(id=f"r{i}", text=f"right {i}", confidence=conf, bbox=BBox(310, i * 25, 100, 15))
        for i in range(4)
    ]
    page = DocumentPage(number=1, width=600, height=200, lines=tuple(left_col + right_col))
    doc = DocumentStructure(pages=(page,))
    context = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")

    res = DocumentAssembler().assemble(doc, context)

    assert isinstance(res, Ok)
    assembled_page = res.value.pages[0]
    assert assembled_page.paragraphs == ()
    assert assembled_page.lines == page.lines
