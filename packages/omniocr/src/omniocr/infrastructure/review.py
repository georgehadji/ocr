"""Review helpers and data models for the human-correction UI.

Per BUILD_PLAN §4.15 and ARCHITECTURE.md §3.8: the review UI displays
image lines alongside recognized text, highlights low-confidence regions,
shows disagreements between engines, and lets the scholar accept or reject
suggestions. All editor operations emit ``Suggestion`` objects — the
source text is never mutated in place.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Tuple

from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    PageFailure,
    Suggestion,
)


@dataclass(frozen=True, slots=True)
class ReviewLine:
    """A single line displayed in the review pane with its metadata."""

    line_id: str
    text: str
    confidence: float
    bbox: BBox
    script: str
    region_type: str
    reading_order: int
    suggestions: Tuple[Suggestion, ...] = field(default_factory=tuple)
    is_low_confidence: bool = False


@dataclass(frozen=True, slots=True)
class ReviewPage:
    """A page with its recognized lines, suggestions, and failures for review."""

    number: int
    width: int
    height: int
    image_bytes: bytes
    lines: Tuple[ReviewLine, ...] = field(default_factory=tuple)
    failures: Tuple[PageFailure, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReviewDocument:
    """Full document assembled for the review UI."""

    pages: Tuple[ReviewPage, ...] = field(default_factory=tuple)


_LOW_CONFIDENCE_THRESHOLD = 50.0


def _is_low_confidence(confidence: Confidence) -> bool:
    return confidence.value < _LOW_CONFIDENCE_THRESHOLD


def build_review_page(
    page: DocumentPage,
    image_bytes: bytes,
    line_suggestions: Mapping[str, Sequence[Suggestion]] | None = None,
) -> ReviewPage:
    """Assemble a ``ReviewPage`` from pipeline output.

    ``line_suggestions`` maps line ids to the suggestions that apply to that
    line so the UI can group suggestions by line rather than displaying a
    flat list.
    """
    mapped = line_suggestions or {}
    review_lines = tuple(
        ReviewLine(
            line_id=line.id,
            text=line.text,
            confidence=line.confidence.value,
            bbox=line.bbox,
            script=line.script.value,
            region_type=line.region_type.value,
            reading_order=line.reading_order,
            suggestions=tuple(mapped.get(line.id, ())),
            is_low_confidence=_is_low_confidence(line.confidence),
        )
        for line in page.lines
    )
    return ReviewPage(
        number=page.number,
        width=page.width,
        height=page.height,
        image_bytes=image_bytes,
        lines=review_lines,
        failures=page.failures,
    )


def build_review_document(
    document: DocumentStructure,
    page_images: Sequence[bytes],
) -> ReviewDocument:
    """Assemble a ``ReviewDocument`` from pipeline output and raw page images."""
    suggestions_by_line: dict[str, list[Suggestion]] = {}
    for page in document.pages:
        for suggestion in page.suggestions:
            suggestions_by_line.setdefault(suggestion.line_id, []).append(suggestion)

    review_pages = tuple(
        build_review_page(
            page, page_images[index] if index < len(page_images) else b"", suggestions_by_line
        )
        for index, page in enumerate(document.pages)
    )
    return ReviewDocument(pages=review_pages)


def group_suggestions_by_reason(
    document: ReviewDocument,
) -> dict[str, int]:
    """Count suggestions grouped by reason across the full document."""
    counts: dict[str, int] = {}
    for page in document.pages:
        for line in page.lines:
            for suggestion in line.suggestions:
                reason = suggestion.reason
                counts[reason] = counts.get(reason, 0) + 1
    return counts


__all__ = [
    "ReviewDocument",
    "ReviewLine",
    "ReviewPage",
    "build_review_document",
    "build_review_page",
    "group_suggestions_by_reason",
]
