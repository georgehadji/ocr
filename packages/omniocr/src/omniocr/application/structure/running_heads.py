from __future__ import annotations

import dataclasses
import difflib
import re
import unicodedata
from typing import Mapping

from omniocr.domain.models import (
    DocumentPage,
    DocumentStructure,
    OCRLine,
    ParagraphRole,
    RegionType,
)

# A folio such as "— 6 —" keeps its dash-wrapped shape even when OCR misreads
# the number ("- ΛΦ -"), and a misread number repeats on no other page.
_FOLIO = re.compile(r"^[-‐–—－]+\s*\S{1,4}\s*[-‐–—－]+$")
# Latin capitals OCR substitutes for their Greek look-alikes, after lowercasing.
_LATIN_TO_GREEK = str.maketrans("abehikmnoptxyz", "αβεηικμνορτχυζ")
_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
# A footnote opens with its number and reads as running (lower-case) text, so
# a citation it repeats ("22 Όπ.π., σ. 269.") is not a head. shortcut: a head
# in lower case after a folio number ("12 Ιλιάδος") is missed; use layout
# position instead if books like that turn up.
_FOOTNOTE = re.compile(r"^\d{1,3}\s+\S")


def normalize_candidate(text: str) -> str:
    """Normalize text by lowercasing and replacing numbers and Roman numerals with '@'.

    This allows variable page numbers (arabic and roman) to match across pages.
    Accents are dropped and, on a line with Greek in it, Latin look-alikes are
    read as Greek, so an OCR misreading of a head still matches the head. This
    key is only compared, never shown: the recognized text is untouched.
    """
    t = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    ).strip()
    if _GREEK.search(t):
        t = t.translate(_LATIN_TO_GREEK)
    # Replace arabic digit runs with '@'
    t = re.sub(r"\b\d+\b", "@", t)
    # Replace standalone Roman numerals with '@'
    t = re.sub(r"\b[ivxlcdm]+\b", "@", t)
    return t


def check_similarity(s1: str, s2: str) -> float:
    """Compute SequenceMatcher ratio with a quick length-tolerance filter."""
    if abs(len(s1) - len(s2)) > 4:
        return 0.0
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def get_slot_line(page: DocumentPage, slot_type: str, index: int) -> OCRLine | None:
    """Get the OCRLine corresponding to a top or bottom index of a page."""
    if not page.lines:
        return None
    if slot_type == "top":
        if 0 <= index < len(page.lines):
            return page.lines[index]
    elif slot_type == "bottom":
        if 0 <= index < len(page.lines):
            return page.lines[len(page.lines) - 1 - index]
    return None


def detect(
    document: DocumentStructure,
    *,
    top_k: int = 2,
    bottom_k: int = 2,
    window: int = 2,
    threshold: float = 0.9,
) -> Mapping[tuple[int, str], RegionType]:
    """Identify lines at the top/bottom of pages that repeat across a window of pages.

    Keys are ``(page number, line id)``: engines number lines per page, so a
    line id alone (``line-3-kraken``) names a line on every page.

    A line counts when it matches an edge line on *any* page in the window, not
    on average: books often alternate heads between facing pages (author on
    the verso, title on the recto), so a true head matches only every second
    page. It is compared against all of a neighbour's edge slots, because the
    head and folio swap order between pages (a text layer may list the folio
    first). Folios are also recognized by shape alone.
    """
    results: dict[tuple[int, str], RegionType] = {}
    pages = document.pages
    if len(pages) < 2:
        return results

    for p, page in enumerate(pages):
        for slot_type, k in [("top", top_k), ("bottom", bottom_k)]:
            for i in range(k):
                line_p = get_slot_line(page, slot_type, i)
                if line_p is None or not line_p.text.strip():
                    continue
                if _FOLIO.match(line_p.text.strip()):
                    results[(page.number, line_p.id)] = RegionType.RUNNING_HEAD
                    continue

                text_p = line_p.text.strip()
                if _FOOTNOTE.match(text_p) and any(c.islower() for c in text_p):
                    continue

                normalized_p = normalize_candidate(line_p.text)
                if not normalized_p:
                    continue

                best = 0.0
                for q in range(max(0, p - window), min(len(pages), p + window + 1)):
                    if q == p:
                        continue
                    for j in range(k):
                        line_q = get_slot_line(pages[q], slot_type, j)
                        if line_q and line_q.text:
                            similarity = check_similarity(
                                normalized_p, normalize_candidate(line_q.text)
                            )
                            best = max(best, similarity)

                if best >= threshold:
                    results[(page.number, line_p.id)] = RegionType.RUNNING_HEAD

    return results


def without_running_heads(document: DocumentStructure) -> DocumentStructure:
    """Drop the lines and paragraphs an assembler marked as running heads or folios.

    Opt-in, for a reading copy. The heads go from ``lines`` too, so no
    exporter falls back to them on a page left with no paragraphs; body text
    is never touched.
    """
    pages = tuple(
        dataclasses.replace(
            page,
            lines=tuple(line for line in page.lines if line.region_type != RegionType.RUNNING_HEAD),
            paragraphs=tuple(
                para
                for para in page.paragraphs
                if para.role not in (ParagraphRole.RUNNING_HEAD, ParagraphRole.PAGE_NUMBER)
            ),
        )
        for page in document.pages
    )
    return dataclasses.replace(document, pages=pages)
