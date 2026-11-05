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

import html
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Tuple

from omniocr.domain.corrections import Correction
from omniocr.domain.errors import IngestError, TrainingError
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
from omniocr.domain.result import Result
from omniocr.infrastructure.ingest import DocumentPageSource, ImagePage
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
        The number of corrections appended, or the store error. The batch is
        one transaction: on an error nothing was saved, so retrying does not
        duplicate the rows a half-finished first attempt wrote.

    Note:
        The store is append-only by design, so calling this twice after a
        success appends a second generation of rows rather than updating the
        first. That is the intended audit trail: a revised transcription is a
        new ``Correction`` with a later timestamp, and consumers order by
        ``corrected_at``.
    """
    corrections = [
        review_line_to_correction(
            review_line=line,
            page_number=page.number,
            corrected_text=reviewed_text[line.line_id],
            corrected_by=corrected_by,
        )
        for page in document.pages
        for line in page.lines
        if line.line_id in reviewed_text
    ]
    return store.append_many(corrections)


def confidence_style(confidence: float) -> tuple[str, str]:
    """The (badge, bar colour) a line is drawn with at ``confidence``."""
    if confidence < 40:
        return "🔴", "#ff4444"
    if confidence < 60:
        return "🟡", "#ffaa00"
    if confidence < 80:
        return "🟢", "#44aa44"
    return "✅", "#228822"


def line_view_html(display: str, confidence: float, changed: bool) -> str:
    """The read-only line block. The OCR text is escaped here, once.

    The block is rendered with ``unsafe_allow_html``, so text such as
    ``καὶ <τὸν> λόγον`` would otherwise be parsed as a tag and vanish from the
    page the scholar is checking against the image.
    """
    badge, bar = confidence_style(confidence)
    text = html.escape(display)
    if changed:
        text = (
            "<span style='background:#22882222;text-decoration:underline;"
            f"text-decoration-color:#228822'>{text}</span>"
        )
    return (
        f'<div style="border-left:4px solid {bar};padding:4px 8px 0;margin:2px 0;'
        f'background:linear-gradient(90deg,{bar}08,transparent)">'
        f'<span style="color:{bar};font-weight:bold">{badge} {confidence:.0f}%</span> {text}</div>'
    )


@dataclass(frozen=True, slots=True)
class UndoEntry:
    """One reversible write: ``previous`` is None when the key was absent."""

    store: str
    key: str
    previous: str | None


class UndoLog:
    """Undo for the review stores, in the order the writes happened.

    Undoing by "last key in the dict" is wrong once a key is written twice:
    the dict keeps the key's first position, so the true last action is not
    the last key, and removing it throws away the earlier value as well.
    """

    def __init__(self) -> None:
        self._entries: list[UndoEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def set(
        self,
        stores: Mapping[str, MutableMapping[str, str]],
        store: str,
        key: str,
        value: str | None,
    ) -> None:
        """Write ``value`` to ``stores[store][key]`` (None removes it) and log it."""
        target = stores[store]
        previous = target.get(key)
        if value == previous:
            return
        self._entries.append(UndoEntry(store, key, previous))
        if value is None:
            del target[key]
        else:
            target[key] = value

    def undo(self, stores: Mapping[str, MutableMapping[str, str]]) -> UndoEntry | None:
        """Reverse the most recent write and return it; None when there is none."""
        if not self._entries:
            return None
        entry = self._entries.pop()
        if entry.previous is None:
            stores[entry.store].pop(entry.key, None)
        else:
            stores[entry.store][entry.key] = entry.previous
        return entry

    def clear(self) -> None:
        self._entries.clear()


def page_images(document: bytes, dpi: int = 150) -> list[bytes]:
    """One image per page for the review pane; ``b""`` where a page won't render.

    A PDF is rendered with PyMuPDF. Anything else is an image, read with the
    frame handling the pipeline uses, so a multi-page TIFF gives one image per
    page here exactly as it gives one page to the pipeline. Returns ``[]`` when
    the document cannot be read at all (or PyMuPDF is missing for a PDF); the
    pane then shows its "no page image" notice.
    """
    if document[:4] == b"%PDF":
        try:
            import fitz

            source = fitz.open(stream=document, filetype="pdf")
        except Exception:
            return []
        images: list[bytes] = []
        try:
            for page in source:
                try:
                    images.append(page.get_pixmap(dpi=dpi).tobytes("png"))
                except Exception:
                    images.append(b"")
        finally:
            source.close()
        return images
    try:
        return [
            page.content if isinstance(page, ImagePage) else b""
            for page in DocumentPageSource(text_layer=False).stream(document)
        ]
    except IngestError:
        return []


__all__ = [
    "ReviewDocument",
    "ReviewLine",
    "ReviewPage",
    "UndoEntry",
    "UndoLog",
    "build_review_document",
    "build_review_page",
    "confidence_style",
    "group_suggestions_by_reason",
    "line_view_html",
    "page_images",
    "persist_reviewed_lines",
    "review_line_to_correction",
]
