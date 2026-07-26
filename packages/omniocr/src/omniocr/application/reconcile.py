from __future__ import annotations

from typing import Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import OCRLine, TenantContext
from omniocr.domain.result import Err, Ok, Result


class ConfidenceWeightedReconciler:
    """Choose the highest-confidence candidate while preserving its provenance."""

    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]:
        if not candidates:
            return Err(EngineError("no OCR candidates available"))
        return Ok(max(candidates, key=lambda candidate: candidate.confidence.value))


__all__ = ["ConfidenceWeightedReconciler"]
