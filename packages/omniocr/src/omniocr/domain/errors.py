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
