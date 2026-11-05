from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping, Protocol, Sequence

from omniocr.domain.corrections import Correction
from omniocr.domain.corpus import CorpusPage, SplitName
from omniocr.domain.errors import EngineError, ExportError, IngestError, LayoutError, TrainingError
from omniocr.domain.models import (
    BBox,
    DocumentStructure,
    ModelRef,
    OCRBlock,
    OCRLine,
    Script,
    Suggestion,
    TenantContext,
)
from omniocr.domain.result import Result
from omniocr.domain.training import EvaluationReport, ModelCandidate, PromotedModel, TrainingSample


class RawPage(Protocol):
    @property
    def number(self) -> int: ...

    @property
    def content(self) -> bytes: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...


class IPageSource(Protocol):
    def stream(self, document: bytes) -> Iterator[RawPage]: ...


class IImageProcessor(Protocol):
    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]: ...


class IOCREngine(Protocol):
    name: str

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]: ...


class ILayoutAnalyzer(Protocol):
    def segment(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRLine], LayoutError]: ...


class IRouter(Protocol):
    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]: ...


class IReconciler(Protocol):
    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]: ...


class IPostCorrector(Protocol):
    def correct(
        self, line: OCRLine, context: TenantContext
    ) -> Result[Sequence[Suggestion], EngineError]: ...


class ILexicon(Protocol):
    """Read-only vocabulary used to highlight, never rewrite, OCR text."""

    name: str

    def contains(self, token: str) -> bool: ...


class IExporter(Protocol):
    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]: ...


class IEventBus(Protocol):
    def publish(self, event: object) -> None: ...


class EngineFamily(str):
    pass


class ILineCropper(Protocol):
    """Crop a page image to a line-level region by bounding box."""

    def crop(self, page_image: bytes, bbox: BBox) -> Result[bytes, TrainingError]: ...


class ICorrectionStore(Protocol):
    """Append-only repository for human-verified corrections."""

    def append(self, correction: Correction) -> Result[None, TrainingError]: ...

    def append_many(self, corrections: Sequence[Correction]) -> Result[int, TrainingError]:
        """Persist all of ``corrections`` or none of them."""
        ...

    def for_document(self, document_id: str) -> Sequence[Correction]: ...

    def all_accepted(self) -> Sequence[Correction]: ...


class ITrainingDataExporter(Protocol):
    """Export training samples into a format consumable by a trainer backend."""

    def export(
        self, samples: Sequence[TrainingSample], out: Path
    ) -> Result[Path, TrainingError]: ...


class ITrainer(Protocol):
    """Train or fine-tune a model. Returns ModelCandidate, never PromotedModel."""

    def train(
        self, data: Path, parent: ModelRef, params: Mapping[str, str]
    ) -> Result[ModelCandidate, TrainingError]: ...


class IModelRegistry(Protocol):
    """Registry for promoted models that the router can query."""

    def register(self, model: PromotedModel) -> Result[None, TrainingError]: ...

    def promoted_for_script(self, script: Script) -> PromotedModel | None: ...

    def promoted_for_typeface(self, typeface: str) -> PromotedModel | None: ...


class IEvaluator(Protocol):
    """Evaluate an OCR engine against a held-out corpus split."""

    def evaluate(
        self, engine: IOCREngine, split: SplitName
    ) -> Result[EvaluationReport, TrainingError]: ...


class ICorpusRepository(Protocol):
    """Repository over structured corpus pages."""

    def pages(self, split: SplitName, script: Script | None = None) -> Sequence[CorpusPage]: ...


class IDocumentAssembler(Protocol):
    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, LayoutError]: ...


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # The queue has no record of the job: never submitted, or past its TTL.
    NOT_FOUND = "not_found"


class IJobQueue(Protocol):
    """Where an edition's API hands a stored upload to its OCR worker.

    The queue carries the *path* of the upload, never its bytes. State comes
    from the queue's own backend, so any number of API processes agree on it.
    Both methods raise ``QueueError`` when the broker is unreachable.
    """

    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None: ...

    def state(self, job_id: str) -> JobState: ...


__all__ = [
    "IJobQueue",
    "JobState",
    "ICorrectionStore",
    "ICorpusRepository",
    "IEvaluator",
    "IEventBus",
    "IExporter",
    "IDocumentAssembler",
    "IImageProcessor",
    "ILexicon",
    "ILayoutAnalyzer",
    "ILineCropper",
    "IModelRegistry",
    "IOCREngine",
    "IPageSource",
    "IPostCorrector",
    "IReconciler",
    "IRouter",
    "ITrainer",
    "ITrainingDataExporter",
    "RawPage",
]
