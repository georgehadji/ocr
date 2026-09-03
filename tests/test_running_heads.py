from __future__ import annotations


from omniocr.application.structure.running_heads import detect, normalize_candidate
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    RegionType,
)


def test_normalize_candidate_converts_digits_and_roman_numerals() -> None:
    assert normalize_candidate("12") == "@"
    assert normalize_candidate("xiv") == "@"
    assert normalize_candidate("page 45") == "page @"
    assert normalize_candidate("book iii") == "book @"
    assert normalize_candidate("normal text") == "normal text"


def test_detect_running_heads_and_page_numbers() -> None:
    # Arrange: Build a document of 4 pages where the first line of each page
    # is a running head or a page number (e.g., changing), and the other lines
    # are unique body text.
    conf = Confidence(100.0)
    bbox = BBox(0, 0, 100, 20)

    p1_lines = (
        OCRLine(id="p1-l1", text="ΙΛΙΑΔΟΣ Α", confidence=conf, bbox=bbox),  # Running head
        OCRLine(id="p1-l2", text="1", confidence=conf, bbox=bbox),  # Page number
        OCRLine(id="p1-l3", text="Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος", confidence=conf, bbox=bbox),
    )
    p2_lines = (
        OCRLine(id="p2-l1", text="ΙΛΙΑΔΟΣ Α", confidence=conf, bbox=bbox),  # Running head
        OCRLine(id="p2-l2", text="2", confidence=conf, bbox=bbox),  # Page number
        OCRLine(
            id="p2-l3", text="οὐλομένην, ἣ μυρί’ Ἀχαιοῖς ἄλγε’ ἔθηκεν", confidence=conf, bbox=bbox
        ),
    )
    p3_lines = (
        OCRLine(id="p3-l1", text="ΙΛΙΑΔΟΣ Α", confidence=conf, bbox=bbox),  # Running head
        OCRLine(id="p3-l2", text="3", confidence=conf, bbox=bbox),  # Page number
        OCRLine(
            id="p3-l3", text="πολλὰς δ’ ἰφθίμους ψυχὰς Ἄϊδι προΐαψεν", confidence=conf, bbox=bbox
        ),
    )
    p4_lines = (
        OCRLine(id="p4-l1", text="ΙΛΙΑΔΟΣ Α", confidence=conf, bbox=bbox),  # Running head
        OCRLine(id="p4-l2", text="4", confidence=conf, bbox=bbox),  # Page number
        OCRLine(
            id="p4-l3", text="ἡρώων, αὐτοὺς δὲ ἑλώρια τεῦχε κύνεσσιν", confidence=conf, bbox=bbox
        ),
    )

    doc = DocumentStructure(
        pages=(
            DocumentPage(number=1, width=100, height=100, lines=p1_lines),
            DocumentPage(number=2, width=100, height=100, lines=p2_lines),
            DocumentPage(number=3, width=100, height=100, lines=p3_lines),
            DocumentPage(number=4, width=100, height=100, lines=p4_lines),
        )
    )

    # Act: detect with window=2
    decisions = detect(doc, window=2, top_k=2, bottom_k=0, threshold=0.8)

    # Assert
    # The running heads "ΙΛΙΑΔΟΣ Α" on top-0 should be detected
    assert decisions.get("p1-l1") == RegionType.RUNNING_HEAD
    assert decisions.get("p2-l1") == RegionType.RUNNING_HEAD
    assert decisions.get("p3-l1") == RegionType.RUNNING_HEAD
    assert decisions.get("p4-l1") == RegionType.RUNNING_HEAD

    # The page numbers (1, 2, 3, 4) on top-1 normalize to @, so they match and are detected
    assert decisions.get("p1-l2") == RegionType.RUNNING_HEAD
    assert decisions.get("p2-l2") == RegionType.RUNNING_HEAD
    assert decisions.get("p3-l2") == RegionType.RUNNING_HEAD
    assert decisions.get("p4-l2") == RegionType.RUNNING_HEAD

    # Body lines should NOT be marked as running heads
    assert "p1-l3" not in decisions
    assert "p2-l3" not in decisions
    assert "p3-l3" not in decisions
    assert "p4-l3" not in decisions
