"""Correction and ground-truth value objects for human-verified transcriptions.

Fixes D10 at the type level: ``GroundTruthLine`` is constructible ONLY from
an accepted ``Correction``, never from an ``OCRLine``. This makes the v1 bug
of training on raw OCR output structurally unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from omniocr.domain.models import BBox, Script


@dataclass(frozen=True, slots=True)
class Correction:
    """A human-verified transcription for one line. The only ground-truth source.

    ``original_text`` is what the engine produced — kept for audit.
    ``corrected_text`` is what the human affirmed (may equal ``original_text``
    when no change was needed — human *affirmation* is the signal).

    Corrections are never updated in place; a revised transcription is a new
    ``Correction`` with a later ``corrected_at`` timestamp.
    """

    line_id: str
    page_number: int
    original_text: str
    corrected_text: str
    corrected_by: str
    corrected_at: str
    bbox: BBox
    script: Script
    accepted: bool = True

    def __post_init__(self) -> None:
        if not self.line_id:
            raise ValueError("line_id must not be empty")
        if not self.corrected_by:
            raise ValueError("corrected_by must not be empty")
        # Validate timestamp format (ISO 8601)
        try:
            datetime.fromisoformat(self.corrected_at)
        except (ValueError, TypeError):
            raise ValueError(f"corrected_at must be ISO 8601: {self.corrected_at!r}")


@dataclass(frozen=True, slots=True)
class GroundTruthLine:
    """Training-eligible line. Constructible ONLY from an accepted Correction.

    The type system enforces that there is no path from ``OCRLine`` to
    ``GroundTruthLine`` — the sole constructor is ``from_correction``.
    """

    line_id: str
    text: str
    bbox: BBox
    script: Script
    source_page: int

    @staticmethod
    def from_correction(correction: Correction) -> GroundTruthLine | None:
        """Build a training sample from a human-affirmed correction.

        Returns ``None`` (not an exception) when the correction is rejected
        or empty — this is a filter, not an error.
        """
        if not correction.accepted:
            return None
        text = correction.corrected_text.strip()
        if not text:
            return None
        return GroundTruthLine(
            line_id=correction.line_id,
            text=text,
            bbox=correction.bbox,
            script=correction.script,
            source_page=correction.page_number,
        )


__all__ = [
    "Correction",
    "GroundTruthLine",
]
