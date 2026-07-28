from __future__ import annotations


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


class TrainingError(PipelineError):
    """Base class for training pipeline errors."""


class PromotionRefused(TrainingError):
    """A model candidate did not meet the promotion criteria."""


class CorrectionStoreError(TrainingError):
    """Persistence failure in the corrections store."""

