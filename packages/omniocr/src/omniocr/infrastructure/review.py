"""Review helpers and data models for the human-correction UI.

Per BUILD_PLAN §4.15 and ARCHITECTURE.md §3.8: the review UI displays
image lines alongside recognized text, highlights low-confidence regions,
shows disagreements between engines, and lets the scholar accept or reject
suggestions. All editor operations emit ``Suggestion`` objects — the
source text is never mutated in place.

v2 addition: review actions (accept/edit) also emit ``Correction`` objects
as the durable ground-truth record for training.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Tuple

from omniocr.domain.corrections import Correction
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRBlock,
    PageFailure,
    Script,
    Suggestion,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ICorrectionStore


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
    # Per-engine blocks, carried through so the UI can show engine disagreement
    # (ARCHITECTURE.md §3.8). Empty when a single engine produced the line.
    blocks: Tuple[OCRBlock, ...] = field(default_factory=tuple)


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
            blocks=line.blocks,
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


def review_line_to_correction(
    review_line: ReviewLine,
    page_number: int,
    corrected_text: str,
    corrected_by: str,
    accepted: bool = True,
) -> Correction:
    """Create a ``Correction`` from a review line and user's final text.

    This is the function that review UI controllers call when a user accepts
    or edits a line. It converts the review action into a durable
    ``Correction`` value object that feeds the training pipeline.

    Args:
        review_line: The line as displayed in the review UI.
        page_number: The page this line belongs to.
        corrected_text: The text after human review (may equal original).
        corrected_by: Identifier for the reviewer.
        accepted: Whether the correction is accepted for training.

    Returns:
        A ``Correction`` ready for persistence via ``ICorrectionStore``.
    """
    script = Script(review_line.script) if review_line.script else Script.UNKNOWN
    return Correction(
        line_id=review_line.line_id,
        page_number=page_number,
        original_text=review_line.text,
        corrected_text=corrected_text,
        corrected_by=corrected_by,
        corrected_at=datetime.now(timezone.utc).isoformat(),
        bbox=review_line.bbox,
        script=script,
        accepted=accepted,
    )


def persist_reviewed_lines(
    store: ICorrectionStore,
    document: ReviewDocument,
    reviewed_text: Mapping[str, str],
    corrected_by: str,
) -> Result[int, TrainingError]:
    """Append the human-reviewed lines to the corrections store.

    This is the link that gives the training pipeline anything to train on.
    ``review_line_to_correction`` and ``SqliteCorrectionStore`` both existed;
    nothing called them together, so accepted corrections lived only in
    Streamlit session state and a JSON session file and never reached the
    store that ``TrainingOrchestrator.all_accepted()`` reads.

    Args:
        store: Append-only corrections repository.
        document: The reviewed document, used to resolve each line's page
            number, bbox and script from its id.
        reviewed_text: line_id → final human text. Lines absent from this map
            were not touched by the reviewer and are not persisted — an
            untouched OCR line is not ground truth (CLAUDE.md rule 1).
        corrected_by: Reviewer identifier.

    Returns:
        The number of corrections appended, or the first store error.

    Note:
        The store is append-only by design, so calling this twice appends a
        second generation of rows rather than updating the first. That is the
        intended audit trail: a revised transcription is a new ``Correction``
        with a later timestamp, and consumers order by ``corrected_at``.
    """
    appended = 0
    for page in document.pages:
        for line in page.lines:
            final_text = reviewed_text.get(line.line_id)
            if final_text is None:
                continue
            correction = review_line_to_correction(
                review_line=line,
                page_number=page.number,
                corrected_text=final_text,
                corrected_by=corrected_by,
            )
            result = store.append(correction)
            if isinstance(result, Err):
                return Err(result.error)
            appended += 1
    return Ok(appended)


__all__ = [
    "ReviewDocument",
    "ReviewLine",
    "ReviewPage",
    "build_review_document",
    "build_review_page",
    "group_suggestions_by_reason",
    "persist_reviewed_lines",
    "review_line_to_correction",
]
