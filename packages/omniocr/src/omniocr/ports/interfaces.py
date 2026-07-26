from __future__ import annotations

from typing import Iterator, Protocol, Sequence

from omniocr.domain.models import DocumentStructure, OCRBlock, OCRLine, Suggestion, TenantContext
from omniocr.domain.result import Result
from omniocr.domain.errors import EngineError, ExportError, IngestError, LayoutError


class RawPage(Protocol):
    number: int
    content: bytes
    width: int
    height: int


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


class IExporter(Protocol):
    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]: ...


class IJobStore(Protocol):
    def checkpoint(self, job_id: str, document: DocumentStructure) -> Result[None, IngestError]: ...

    def load(self, job_id: str) -> DocumentStructure | None: ...


class IEventBus(Protocol):
    def publish(self, event: object) -> None: ...


class EngineFamily(str):
    pass


__all__ = [
    "IEventBus",
    "IExporter",
    "IImageProcessor",
    "IJobStore",
    "ILayoutAnalyzer",
    "IOCREngine",
    "IPageSource",
    "IPostCorrector",
    "IReconciler",
    "IRouter",
    "RawPage",
]
