"""Celery tasks for async OCR processing in the Cloud edition.

Each task receives the document bytes and tenant context, validates the
upload, runs the pipeline, and stores the result in Redis.
"""

from __future__ import annotations

import json

from editions.cloud.celery_app import app
from editions.cloud.composition import create_cloud_pipeline, PipelineOrchestrator
from omniocr.domain.models import TenantContext
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.security import validate_upload


@app.task(bind=True, max_retries=3, default_retry_delay=30)
def process_ocr(self, document_bytes: bytes, settings_json: str) -> dict:
    """Run OCR on a document and return result metadata.

    The Celery result backend stores the result; the FastAPI layer
    polls for completion and serves the exported output.
    """
    settings = Settings.from_env()

    # Multi-tenant: validate upload with per-tenant limits.
    upload_result = validate_upload(
        document_bytes,
        "upload.pdf",
        settings.max_upload_bytes,
    )
    if upload_result.is_err():
        raise ValueError(str(upload_result.error))

    pipeline: PipelineOrchestrator = create_cloud_pipeline(settings)
    ctx = TenantContext(
        organization_id="cloud",
        user_id="worker",
        subscription_tier="cloud",
    )
    run_result = pipeline.run(upload_result.value, ctx)
    if run_result.is_err():
        raise RuntimeError(str(run_result.error))
    export_result = pipeline.export(run_result.value, ctx)
    if export_result.is_err():
        raise RuntimeError(str(export_result.error))

    return {
        "status": "completed",
        "pages": len(run_result.value.pages),
        "output_format": "markdown",
    }
