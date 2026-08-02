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
from omniocr.infrastructure.jobs import RedisJobStore, SQLiteJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.tesseract import TesseractEngine
from omniocr.ports.interfaces import IJobStore


def _cloud_job_store(settings: Settings) -> IJobStore:
    """Return a Redis-backed job store for Cloud workers, falling back to SQLite.

    Per the implementation plan (B3), the Cloud edition uses RedisJobStore for
    durable, distributed checkpoint persistence across Celery worker restarts.
    If Redis is unavailable (no redis-py, or the broker is unreachable), fall
    back to SQLite so a standalone Cloud deployment still persists checkpoints.

    This helper deliberately avoids ``structlog`` so the Celery worker path
    (``editions/cloud/tasks.py``) does not acquire a structlog hard-dependency;
    structlog is a dev-only extra.
    """
    try:
        store = RedisJobStore(redis_url=settings.redis_url)
        if store.ping():
            return store
        return SQLiteJobStore("omniocr_cloud_jobs.db")
    except Exception:  # pragma: no cover - environment dependent
        return SQLiteJobStore("omniocr_cloud_jobs.db")


def create_cloud_pipeline(
    settings: Settings | None = None,
) -> PipelineOrchestrator:
    """Build a multi-tenant cloud pipeline with full engine ensemble.

    Unlike the desktop/server editions, the cloud pipeline:
    - Uses a distributed ``RedisJobStore`` for checkpoint persistence
      (falls back to SQLite if Redis is unreachable)
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
        job_store=_cloud_job_store(cfg),
    )
