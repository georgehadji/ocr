"""Celery tasks for async OCR processing in the Cloud edition.

Each task receives the document bytes and tenant context, validates the
upload, runs the pipeline, and stores the result in Redis.
"""

from __future__ import annotations

import base64
from typing import Any

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.security import validate_upload

from editions.cloud.celery_app import app
from editions.cloud.composition import create_cloud_pipeline


@app.task(bind=True, max_retries=3, default_retry_delay=30)  # type: ignore[untyped-decorator]
def process_ocr(self: Any, document_b64: str) -> dict[str, Any]:
    """Run OCR on a base64-encoded document and return result metadata.

    The Celery result backend stores the result; the FastAPI layer
    polls for completion and serves the exported output.

    The payload is base64, not raw ``bytes``, because ``celery_app`` sets
    ``task_serializer="json"`` — enqueuing raw bytes raised ``EncodeError``
    before the worker ever saw the job, so every Cloud submit failed the
    moment Celery was importable.

    There is no settings parameter: the worker's own environment is the
    authority on how it should run, and the previous ``settings_json``
    argument was both never read and passed a live ``Settings`` dataclass,
    which the JSON serializer also rejected.

    Every branch narrows with ``isinstance(..., Err)`` rather than
    ``result.is_err()``: the latter returns a plain ``bool``, so nothing
    downstream is type-checked and a wrong guard reaches production as an
    ``AttributeError`` inside a Celery worker.
    """
    document_bytes = base64.b64decode(document_b64)
    settings = Settings.from_env()

    # Multi-tenant: validate upload with per-tenant limits.
    upload_result = validate_upload(
        document_bytes,
        "upload.pdf",
        settings.max_upload_bytes,
    )
    if isinstance(upload_result, Err):
        raise ValueError(str(upload_result.error))

    pipeline: PipelineOrchestrator = create_cloud_pipeline(settings)
    ctx = TenantContext(
        organization_id="cloud",
        user_id="worker",
        subscription_tier="cloud",
    )
    run_result = pipeline.run(upload_result.value, ctx)
    if isinstance(run_result, Err):
        raise RuntimeError(str(run_result.error))
    export_result = pipeline.export(run_result.value, ctx)
    if isinstance(export_result, Err):
        raise RuntimeError(str(export_result.error))

    return {
        "status": "completed",
        "pages": len(run_result.value.pages),
        "output_format": "markdown",
    }
