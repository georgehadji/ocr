from __future__ import annotations

import difflib
import re
from typing import Mapping

from omniocr.domain.models import DocumentStructure, RegionType, DocumentPage, OCRLine


def normalize_candidate(text: str) -> str:
    """Normalize text by lowercasing and replacing numbers and Roman numerals with '@'.

    This allows variable page numbers (arabic and roman) to match across pages.
    """
    t = text.lower().strip()
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
) -> Mapping[str, RegionType]:
    """Identify lines at the top/bottom of pages that repeat across a window of pages."""
    results: dict[str, RegionType] = {}
    pages = document.pages
    if len(pages) < 2:
        return results

    # We evaluate each page and each slot
    for p, page in enumerate(pages):
        for slot_type, k in [("top", top_k), ("bottom", bottom_k)]:
            for i in range(k):
                line_p = get_slot_line(page, slot_type, i)
                if line_p is None or not line_p.text:
                    continue

                normalized_p = normalize_candidate(line_p.text)
                if not normalized_p:
                    continue

                total_similarity = 0.0
                valid_neighbors = 0

                for d in range(-window, window + 1):
                    if d == 0:
                        continue
                    q = p + d
                    if 0 <= q < len(pages):
                        valid_neighbors += 1
                        neighbor_page = pages[q]
                        line_q = get_slot_line(neighbor_page, slot_type, i)
                        if line_q and line_q.text:
                            normalized_q = normalize_candidate(line_q.text)
                            total_similarity += check_similarity(normalized_p, normalized_q)

                score = total_similarity / valid_neighbors if valid_neighbors > 0 else 0.0
                if score >= threshold:
                    results[line_p.id] = RegionType.RUNNING_HEAD

    return results
