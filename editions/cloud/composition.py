"""Cloud Edition composition root for OmniOCR.

Multi-tenant pipeline with Celery-backed async processing,
upload validation, and full engine ensemble.

This edition does not wire adapters itself. It delegates to the core
composition root and supplies only what is genuinely cloud-specific — the
distributed job store. Re-wiring the pipeline here would silently drop the
invariants ``create_ensemble_pipeline`` enforces (resilience decorators, the
VLM grounding guard, script lexicons, promoted-model routing).
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.composition import create_ensemble_pipeline
from omniocr.domain.models import Script
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.jobs import RedisJobStore, SQLiteJobStore
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
    """Build a multi-tenant cloud pipeline with the full engine ensemble.

    Cloud-specific choice: a distributed ``RedisJobStore`` for checkpoint
    persistence, falling back to SQLite when Redis is unreachable. Everything
    else — engines, resilience wrapping, routing, post-correction — comes from
    the shared composition root.
    """
    cfg = settings or Settings.from_env()
    return create_ensemble_pipeline(
        tesseract_language=cfg.tesseract_language,
        kraken_model_path=cfg.kraken_model_path,
        script=Script.POLYTONIC,
        job_store=_cloud_job_store(cfg),
        vlm_api_key=cfg.vlm_api_key if cfg.enable_vlm else None,
    )
