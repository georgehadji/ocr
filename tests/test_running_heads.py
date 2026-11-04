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
    assert decisions.get((1, "p1-l1")) == RegionType.RUNNING_HEAD
    assert decisions.get((2, "p2-l1")) == RegionType.RUNNING_HEAD
    assert decisions.get((3, "p3-l1")) == RegionType.RUNNING_HEAD
    assert decisions.get((4, "p4-l1")) == RegionType.RUNNING_HEAD

    # The page numbers (1, 2, 3, 4) on top-1 normalize to @, so they match and are detected
    assert decisions.get((1, "p1-l2")) == RegionType.RUNNING_HEAD
    assert decisions.get((2, "p2-l2")) == RegionType.RUNNING_HEAD
    assert decisions.get((3, "p3-l2")) == RegionType.RUNNING_HEAD
    assert decisions.get((4, "p4-l2")) == RegionType.RUNNING_HEAD

    # Body lines should NOT be marked as running heads
    assert (1, "p1-l3") not in decisions
    assert (2, "p2-l3") not in decisions
    assert (3, "p3-l3") not in decisions
    assert (4, "p4-l3") not in decisions


# Distinct per page: body text that differed only by a number would normalize
# to the same key and, on a three-line page, sit inside the edge slots.
_BODIES = (
    "Κάνοντας στροφὴ πρὸς τὰ δυτικὰ βρισκόμαστε μπροστὰ σὲ μιὰ γωνιά",
    "Στὴν ἄλλη μεριὰ παρατηροῦμε ἕνα σπίτι ὅμοιο μὲ ἐλαιοτριβεῖο",
    "Τώρα ἔχουμε μπροστά μας τὴν Πορτάρα τοῦ κάστρου ποὺ βλέπει",
    "Ἕνας ἀπὸ τοὺς πολλοὺς κι ὁ πύργος μὲ τὴ σειρήνα στὴν κορυφή",
    "Πρὶν κατεβοῦμε στὴν πύλη τοῦ μοναστηριοῦ ρίχνουμε μιὰ ματιά",
    "Τοποθεσία ἐξαίρετη, περίοπτη, ἂν καὶ ὄχι ἡ ὑψηλότερη τοῦ λόφου",
    "Ἐδῶ ἦταν ἡ ἀρχαία δεξαμενὴ τῆς Θεσσαλονίκης κατὰ τὴν παράδοση",
)


def _book(heads_and_folios: list[tuple[str, str]]) -> DocumentStructure:
    """Pages laid out as printed: head on top, body, folio at the bottom."""
    conf = Confidence(100.0)
    bbox = BBox(0, 0, 100, 20)
    pages = []
    for number, (head, folio) in enumerate(heads_and_folios, start=1):
        lines = (
            OCRLine(id=f"p{number}-head", text=head, confidence=conf, bbox=bbox),
            OCRLine(
                id=f"p{number}-body",
                text=_BODIES[number % len(_BODIES)],
                confidence=conf,
                bbox=bbox,
            ),
            OCRLine(id=f"p{number}-folio", text=folio, confidence=conf, bbox=bbox),
        )
        pages.append(DocumentPage(number=number, width=100, height=100, lines=lines))
    return DocumentStructure(pages=tuple(pages))


VERSO = "ΒΑΣΙΛΕΙΟΣ ΔΕΔΟΥΣΗΣ"
RECTO = "ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΣΑΛΟΝΙΚΗΣ"


def test_alternating_verso_recto_heads_are_detected() -> None:
    """Heads that alternate between facing pages repeat every second page.

    Averaging similarity over p±1 and p±2 scored these about 0.5, so a book
    with a different head on each side of the spread had none detected.
    """
    doc = _book([(VERSO if n % 2 == 0 else RECTO, f"— {n} —") for n in range(6, 12)])

    decisions = detect(doc)

    for page in doc.pages:
        assert decisions.get((page.number, f"p{page.number}-head")) == RegionType.RUNNING_HEAD
        assert decisions.get((page.number, f"p{page.number}-folio")) == RegionType.RUNNING_HEAD
        assert (page.number, f"p{page.number}-body") not in decisions


def test_heads_with_ocr_noise_still_match() -> None:
    """Latin look-alikes and stray accents are how OCR misreads a Greek head."""
    heads = [RECTO, VERSO, "ΤΑ BYZANTINA ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΣΑΛΟΝΙΚΗΣ", "ΒΑΣΙΛΕΊΟΣ ΔΕΔΟΥΣΗΣ", RECTO]
    doc = _book([(head, "— 1 —") for head in heads])

    decisions = detect(doc)

    assert decisions.get((3, "p3-head")) == RegionType.RUNNING_HEAD
    assert decisions.get((4, "p4-head")) == RegionType.RUNNING_HEAD


def test_garbled_folio_is_detected_by_its_shape() -> None:
    """A misread folio such as "- ΛΦ -" repeats nothing, but keeps its shape."""
    folios = ["- 40 -", "- Δ1 -", "- ΛΦ -", "－ 7 -", "- 44 -"]
    openings = [
        "Μῆνιν ἄειδε θεὰ",
        "οὐλομένην, ἣ μυρί’",
        "πολλὰς δ’ ἰφθίμους",
        "ἡρώων, αὐτοὺς δὲ",
        "οἰωνοῖσί τε πᾶσι",
    ]
    doc = _book(list(zip(openings, folios)))

    decisions = detect(doc)

    for number in range(1, 6):
        assert decisions.get((number, f"p{number}-folio")) == RegionType.RUNNING_HEAD
        assert (number, f"p{number}-head") not in decisions


def test_a_detection_marks_only_its_own_page() -> None:
    """Engines number lines per page, so ``line-3-kraken`` exists on every page.

    Keyed by line id alone, one page's detected folio marked line 3 of every
    other page as a running head too, hiding body text from the export.
    """
    conf = Confidence(100.0)
    bbox = BBox(0, 0, 100, 20)
    # Every text appears once in the book; only page 2's last line is a folio.
    texts = {
        1: (_BODIES[0], _BODIES[3], _BODIES[6]),
        2: (_BODIES[1], _BODIES[4], "— 2 —"),
        3: (_BODIES[2], _BODIES[5], "Μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος οὐλομένην"),
    }
    folio_on_page_two_only = []
    for number, page_texts in texts.items():
        lines = tuple(
            OCRLine(id=f"line-{i}-kraken", text=text, confidence=conf, bbox=bbox)
            for i, text in enumerate(page_texts, start=1)
        )
        folio_on_page_two_only.append(
            DocumentPage(number=number, width=100, height=100, lines=lines)
        )

    decisions = detect(DocumentStructure(pages=tuple(folio_on_page_two_only)))

    assert decisions == {(2, "line-3-kraken"): RegionType.RUNNING_HEAD}


def test_repeated_footnote_citations_are_not_running_heads() -> None:
    """ "22 Όπ.π., σ. 269." and "23 Όπ.π., σ. 271." match once numbers become '@'.

    A footnote starts with its number and reads as running text, so it is not
    a head however often a citation repeats.
    """
    doc = _book([(_BODIES[n], f"{20 + n}  Όπ.π., σ. {260 + n}.") for n in range(5)])

    decisions = detect(doc)

    assert all(not line_id.endswith("-folio") for _, line_id in decisions)
