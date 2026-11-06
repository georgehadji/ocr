"""Celery task for async OCR processing in the Cloud edition.

The task receives the id, the stored upload's path and the tenant. The API
validated the upload against its real filename before queueing it, so the
worker does not re-check it.
"""

from __future__ import annotations

from pathlib import Path

from omniocr.domain.models import TenantContext
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.prometheus_metrics import PageMetricsBus, worker_metrics

from editions.cloud.celery_app import app
from editions.cloud.composition import create_cloud_pipeline
from editions.common.worker import result_path, run_job


@app.task  # type: ignore[untyped-decorator]
def process_ocr(job_id: str, upload: str, tenant: str) -> None:
    """Recognize the stored upload and write its result for the API to serve.

    There is no settings parameter: the worker's own environment is the
    authority on how it should run. A failed run raises, which Celery records
    as FAILURE. There is no retry: a book takes hours, and rerunning one that
    failed on a bad page just fails again later.
    """
    settings = Settings.from_env()
    context = TenantContext(organization_id=tenant, user_id="worker", subscription_tier="cloud")
    metrics = worker_metrics()
    pipeline = create_cloud_pipeline(settings, PageMetricsBus(metrics))
    run_job(Path(upload), result_path(settings, job_id), pipeline, context, metrics)
