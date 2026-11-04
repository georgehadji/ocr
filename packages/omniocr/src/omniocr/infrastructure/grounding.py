"""Grounding guard for VLM output verification.

Per ARCHITECTURE.md §3.6 and BUILD_PLAN §4.10: VLM output is never the
unaudited sole source. It must be reconciled against box-grounded output
from verifiable engines (Tesseract, Kraken). The grounding guard flags
VLM text whose bounding boxes do not overlap with any verifiable engine
block — such text is stored as a suggestion, never accepted as source.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import BBox, OCRBlock, Suggestion, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import IOCREngine, RawPage

_LOG = logging.getLogger("omniocr.grounding")


class GroundingGuard:
    """Reject VLM blocks whose bounding boxes lack overlap with engine blocks."""

    def __init__(self, overlap_threshold: float = 0.0) -> None:
        if overlap_threshold < 0 or overlap_threshold > 1:
            raise ValueError("overlap_threshold must be in [0, 1]")
        self._threshold = overlap_threshold

    @staticmethod
    def _iou(first: BBox, second: BBox) -> float:
        """Intersection-over-union for two axis-aligned bounding boxes."""
        inter_x = max(0, min(first.right, second.right) - max(first.x, second.x))
        inter_y = max(0, min(first.bottom, second.bottom) - max(first.y, second.y))
        intersection = inter_x * inter_y
        first_area = first.w * first.h
        second_area = second.w * second.h
        union = first_area + second_area - intersection
        return intersection / union if union > 0 else 0.0

    def filter(
        self,
        vlm_blocks: Sequence[OCRBlock],
        engine_blocks: Sequence[OCRBlock],
    ) -> tuple[Sequence[OCRBlock], Sequence[Suggestion]]:
        """Separate VLM blocks into grounded (accepted) and ungrounded (suggestions).

        Returns (grounded_blocks, ungrounded_suggestions).
        """
        grounded: list[OCRBlock] = []
        suggestions: list[Suggestion] = []
        for block in vlm_blocks:
            best_overlap = max(
                (self._iou(block.bbox, e.bbox) for e in engine_blocks),
                default=0.0,
            )
            if best_overlap > self._threshold:
                grounded.append(block)
            else:
                suggestions.append(
                    Suggestion(
                        line_id=block.id,
                        source_text=block.text,
                        suggestion_text="",
                        reason="ungrounded_vlm",
                        reversible=False,
                    )
                )
        return tuple(grounded), tuple(suggestions)


class GroundedEngine(IOCREngine):
    """Keep only the blocks of ``engine`` that a box-grounded ``reference`` corroborates.

    The VLM and Unlimited-OCR place text by what the model says, not by what
    is on the page. This decorator is where "never the unaudited source"
    becomes true: a block over a region the reference engine read as empty is
    dropped before the pipeline can assign it to a line.

    If the reference engine fails there is nothing to ground against, so the
    result is an ``Err``, never the ungrounded blocks.

    ponytail: the reference engine runs a second time per page. Reuse its
    result from the pipeline if Tesseract time ever shows up in a profile.
    """

    def __init__(
        self,
        engine: IOCREngine,
        reference: IOCREngine,
        guard: GroundingGuard | None = None,
    ) -> None:
        self.engine = engine
        self.name = engine.name
        self._reference = reference
        self._guard = guard or GroundingGuard()

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        result = self.engine.extract(page, context)
        if isinstance(result, Err) or not result.value:
            return result
        reference = self._reference.extract(page, context)
        if isinstance(reference, Err):
            return Err(
                EngineError(
                    f"{self.name} output cannot be grounded: "
                    f"{self._reference.name} failed: {reference.error}"
                )
            )
        grounded, ungrounded = self._guard.filter(result.value, reference.value)
        if ungrounded:
            _LOG.warning(
                "ungrounded_blocks_dropped",
                extra={"engine": self.name, "page": page.number, "dropped": len(ungrounded)},
            )
        return Ok(grounded)


__all__ = ["GroundedEngine", "GroundingGuard"]
