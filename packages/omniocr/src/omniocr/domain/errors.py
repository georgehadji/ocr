from __future__ import annotations

from enum import StrEnum


class RefusalReason(StrEnum):
    """Why a candidate model was not promoted. The strings are a frozen contract."""

    WRONG_SPLIT = "wrong_split"
    TOO_FEW_SAMPLES = "too_few_samples"
    SCRIPT_REGRESSED = "script_regressed"
    BELOW_THRESHOLD = "below_threshold"
    PARENT_CER_ZERO = "parent_cer_zero"


class PipelineError(Exception):
    """Base class for OCR pipeline errors."""


class IngestError(PipelineError):
    pass


class EngineError(PipelineError):
    pass


class LayoutError(PipelineError):
    pass


class ExportError(PipelineError):
    pass


class QueueError(PipelineError):
    """The job queue could not take, or report on, a job (broker unreachable)."""


class TrainingError(PipelineError):
    """Base class for training pipeline errors."""


class PromotionRefused(TrainingError):
    """A model candidate did not meet the promotion criteria.

    ``reason`` names the rule that refused it, so a caller can branch on it
    instead of parsing the message; ``None`` for a refusal built without one.
    """

    def __init__(self, message: str, reason: RefusalReason | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class CorrectionStoreError(TrainingError):
    """Persistence failure in the corrections store."""
