"""Cloud Edition composition root for OmniOCR.

Multi-tenant pipeline with Celery-backed async processing,
upload validation, and full engine ensemble.
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.domain.models import Script
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import SQLiteJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.tesseract import TesseractEngine


def create_cloud_pipeline(
    settings: Settings | None = None,
) -> PipelineOrchestrator:
    """Build a multi-tenant cloud pipeline with full engine ensemble.

    Unlike the desktop/server editions, the cloud pipeline:
    - Uses a per-tenant ``SQLiteJobStore`` for checkpoint persistence
    - Runs all available engines for maximum accuracy
    - Wraps every engine in ``RetryingEngine`` for resilience
    """
    cfg = settings or Settings.from_env()

    tesseract = RetryingEngine(TesseractEngine("grc+ell+eng"))
    kraken = RetryingEngine(KrakenEngine(""))

    router = ScriptRouter(
        by_script={
            Script.ANCIENT: (kraken, tesseract),
            Script.BYZANTINE: (kraken, tesseract),
            Script.POLYTONIC: (kraken, tesseract),
        },
        default=(tesseract,),
    )

    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=KrakenLayoutAnalyzer(),
        router=router,
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=MarkdownExporter(),
        job_store=SQLiteJobStore("omniocr_cloud_jobs.db"),
    )
