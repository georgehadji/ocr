"""Tests for the review UI data model and helpers."""

from __future__ import annotations

from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    RegionType,
    Script,
    Suggestion,
)
from omniocr.infrastructure.review import (
    build_review_document,
    build_review_page,
    group_suggestions_by_reason,
)


def _page(number: int = 1) -> DocumentPage:
    return DocumentPage(
        number=number,
        width=600,
        height=800,
        lines=(
            OCRLine(
                id=f"line-{number}-1",
                text="Ἑλληνικά",
                confidence=Confidence(92.0),
                bbox=BBox(10, 20, 300, 30),
                script=Script.POLYTONIC,
                region_type=RegionType.MAIN_TEXT,
                reading_order=1,
            ),
            OCRLine(
                id=f"line-{number}-2",
                text="low conf text",
                confidence=Confidence(30.0),
                bbox=BBox(10, 60, 200, 20),
                script=Script.POLYTONIC,
                region_type=RegionType.APPARATUS,
                reading_order=2,
            ),
        ),
        suggestions=(
            Suggestion(
                line_id="line-1-1",
                source_text="Ἑλληνικά",
                suggestion_text="ἑλληνικά",
                reason="check_casing",
                reversible=True,
            ),
        ),
    )


def test_build_review_page_structure() -> None:
    """ReviewPage preserves page metadata and line details."""
    page = _page()
    review = build_review_page(page, b"image-data")

    assert review.number == 1
    assert review.width == 600
    assert review.height == 800
    assert review.image_bytes == b"image-data"
    assert len(review.lines) == 2


def test_review_line_confidence_flag() -> None:
    """Lines below 50% confidence are marked as low confidence."""
    page = _page()
    review = build_review_page(page, b"")

    assert review.lines[0].text == "Ἑλληνικά"
    assert review.lines[0].confidence == 92.0
    assert not review.lines[0].is_low_confidence

    assert review.lines[1].text == "low conf text"
    assert review.lines[1].confidence == 30.0
    assert review.lines[1].is_low_confidence


def test_review_line_preserves_metadata() -> None:
    """Script, region type, and reading order are preserved."""
    review = build_review_page(_page(), b"")

    assert review.lines[0].script == "polytonic"
    assert review.lines[0].region_type == "main"
    assert review.lines[0].reading_order == 1
    assert review.lines[1].region_type == "apparatus"
    assert review.lines[1].reading_order == 2


def test_review_line_suggestions_grouped_by_id() -> None:
    """Suggestions are assigned to the correct line by line_id."""
    suggestions = {
        "line-1-1": (Suggestion("line-1-1", "source", "suggestion", "check", True),),
    }
    review = build_review_page(_page(), b"", suggestions)

    matching = [line for line in review.lines if line.line_id == "line-1-1"]
    assert len(matching) == 1
    assert len(matching[0].suggestions) == 1
    assert matching[0].suggestions[0].reason == "check"


def test_build_review_document_assembles_multiple_pages() -> None:
    """ReviewDocument correctly stitches pages together."""
    doc = DocumentStructure(pages=(_page(1), _page(2)))
    review = build_review_document(doc, [b"img-1", b"img-2"])

    assert len(review.pages) == 2
    assert review.pages[0].number == 1
    assert review.pages[0].image_bytes == b"img-1"
    assert review.pages[1].number == 2
    assert review.pages[1].image_bytes == b"img-2"


def test_group_suggestions_by_reason_counts() -> None:
    """Suggestion counts are correctly tallied across pages."""
    doc = DocumentStructure(pages=(_page(1), _page(2)))
    review = build_review_document(doc, [b"", b""])
    counts = group_suggestions_by_reason(review)

    assert counts.get("check_casing", 0) == 2  # one per page


def test_build_review_page_empty_lines() -> None:
    """A page with no lines produces an empty review page."""
    page = DocumentPage(number=1, width=100, height=100)
    review = build_review_page(page, b"")
    assert len(review.lines) == 0
    assert len(review.failures) == 0
