"""Server Edition composition root for OmniOCR.

Wires the pipeline with RQ-backed async job processing,
API key validation, upload validation, and configurable engines.
"""

from __future__ import annotations

from pathlib import Path

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.composition import create_ensemble_pipeline
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.prometheus_metrics import PageMetricsBus, worker_metrics
from omniocr.ports.interfaces import IEventBus

from editions.common.worker import result_path, run_job


def create_server_pipeline(
    settings: Settings | None = None,
    event_bus: IEventBus | None = None,
) -> PipelineOrchestrator:
    """Build a server-grade pipeline with retry resilience.

    Queuing is handled externally by RQ; the pipeline runs synchronously
    inside the worker process. Engines, routing and post-correction come from
    the shared composition root, so this edition cannot drift from the
    invariants the core enforces.
    """
    cfg = settings or Settings.from_env()
    return create_ensemble_pipeline(
        tesseract_language=cfg.tesseract_language,
        kraken_model_path=cfg.kraken_model_path,
        script=Script.POLYTONIC,
        vlm_api_key=cfg.vlm_api_key if cfg.enable_vlm else None,
        unlimited_ocr_url=cfg.unlimited_ocr_url if cfg.enable_vlm else None,
        event_bus=event_bus,
        workers=cfg.max_workers,
    )


def run_ocr_job(job_id: str, upload: str, tenant: str) -> None:
    """Called by the RQ worker: recognize the stored upload, write its result.

    The API validated the upload against its real filename before queueing it,
    so the worker does not re-check it. Takes no settings argument: the
    worker's own environment is the authority on how it should run.
    """
    settings = Settings.from_env()
    context = TenantContext(organization_id=tenant, user_id="worker", subscription_tier="server")
    metrics = worker_metrics()
    pipeline = create_server_pipeline(settings, PageMetricsBus(metrics))
    run_job(Path(upload), result_path(settings, job_id), pipeline, context, metrics)
