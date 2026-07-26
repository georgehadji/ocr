from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator, SingleLineLayoutAnalyzer
from omniocr.infrastructure.tesseract import TesseractEngine
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.domain.models import Script
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import InMemoryJobStore
from omniocr.ports.interfaces import IExporter, IJobStore


def create_desktop_pipeline() -> PipelineOrchestrator:
    return PipelineOrchestrator(exporter=MarkdownExporter(), job_store=InMemoryJobStore())


def create_tesseract_pipeline(
    language: str = "eng",
    script: Script = Script.MODERN,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
) -> PipelineOrchestrator:
    """Build the desktop pipeline with the optional Tesseract engine enabled."""
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=SingleLineLayoutAnalyzer(script),
        router=_TesseractRouter(TesseractEngine(language)),
        reconciler=ConfidenceWeightedReconciler(),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


def create_ensemble_pipeline(
    tesseract_language: str,
    kraken_model_path: str,
    script: Script = Script.POLYTONIC,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
) -> PipelineOrchestrator:
    """Build a CPU ensemble with script rules injected at the composition root."""
    tesseract = RetryingEngine(TesseractEngine(tesseract_language))
    kraken = RetryingEngine(KrakenEngine(kraken_model_path))
    router = ScriptRouter(
        by_script={
            # Historical Greek benefits from the specialized Kraken model.
            Script.ANCIENT: (kraken, tesseract),
            Script.BYZANTINE: (kraken, tesseract),
            Script.POLYTONIC: (kraken, tesseract),
        },
        default=(tesseract,),
    )
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=SingleLineLayoutAnalyzer(script),
        router=router,
        reconciler=ConfidenceWeightedReconciler(),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


class _TesseractRouter:
    def __init__(self, engine: TesseractEngine) -> None:
        self._engine = engine

    def route(self, line: object, context: object) -> tuple[TesseractEngine, ...]:
        return (self._engine,)
