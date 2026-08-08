"""OmniOCR Cloud Edition — FastAPI application with Celery backend.

Run with:

    uvicorn editions.cloud.main:app --host 0.0.0.0 --port 8000

Requires: fastapi, uvicorn, celery, redis.
Celery worker: celery -A editions.cloud.celery_app worker --loglevel=info
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response

from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.logging import configure_logging

configure_logging("omniocr-cloud")

app = FastAPI(title="OmniOCR Cloud", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"],
)

settings = Settings.from_env()
# ponytail: in-process job index — a second uvicorn worker will not see these.
# Move to Redis if the Cloud edition is ever run with more than one API process.
_jobs: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": settings.app_name, "tenant": "multi"}


@app.post("/ocr/submit", status_code=202)
async def submit_ocr(
    file: UploadFile = File(...),
    job_id: str | None = Form(None),
    tenant_id: str | None = Form("default"),
) -> dict[str, Any]:
    """Submit a document for OCR processing.

    Multi-tenant: each ``tenant_id`` gets isolated job tracking. The
    Celery worker processes the job asynchronously.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Upload is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_bytes} byte limit")

    job_uid = job_id or str(uuid.uuid4())
    job_key = f"{tenant_id}:{job_uid}"

    try:
        from editions.cloud.tasks import process_ocr

        # base64, not raw bytes: the Celery app serializes tasks as JSON.
        task = process_ocr.delay(base64.b64encode(data).decode("ascii"))
        _jobs[job_key] = {
            "status": "queued",
            "filename": file.filename,
            "tenant_id": tenant_id,
            "task_id": task.id,
        }
    except ImportError:
        # Fallback: synchronous processing (for development without Celery)
        from omniocr.domain.models import TenantContext

        from editions.cloud.composition import create_cloud_pipeline

        pipeline = create_cloud_pipeline(settings)

        ctx = TenantContext(
            organization_id=tenant_id or "default", user_id="cloud", subscription_tier="cloud"
        )
        run_result = pipeline.run(data, ctx)
        if isinstance(run_result, Err):
            _jobs[job_key] = {"status": "failed", "error": str(run_result.error)}
            return {"job_id": job_uid, "status": "failed"}

        export_result = pipeline.export(run_result.value, ctx)
        # A failed export is a failed job. This previously substituted b"",
        # wrote an empty .md, and still reported "completed" — the caller
        # downloaded an empty file with no indication anything went wrong.
        if isinstance(export_result, Err):
            _jobs[job_key] = {"status": "failed", "error": str(export_result.error)}
            return {"job_id": job_uid, "status": "failed"}

        result_path = Path("cloud_results") / f"{job_uid}.md"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_bytes(export_result.value)
        _jobs[job_key] = {"status": "completed", "filename": file.filename, "tenant_id": tenant_id}

    return {"job_id": job_uid, "status": _jobs[job_key]["status"]}


@app.get("/ocr/status/{job_id}")
def job_status(job_id: str, tenant_id: str | None = "default") -> dict[str, Any]:
    """Poll the status of an OCR job (scoped to tenant)."""
    job_key = f"{tenant_id}:{job_id}"
    job = _jobs.get(job_key)
    if job is None:
        raise HTTPException(404, "Job not found")

    # A queued job's live state lives in the Celery backend, not in `_jobs`,
    # which is only ever updated at submit time. This previously ran solely in
    # the `job is None` branch and picked `next(j["task_id"] for j in
    # _jobs.values())` — an arbitrary *other* job's task — so it reported a
    # stranger's status under this job's id, and never ran for a job that
    # existed.
    task_id = job.get("task_id")
    if task_id:
        try:
            from celery.result import AsyncResult

            from editions.cloud.celery_app import app as celery_app

            return {
                "job_id": job_id,
                **job,
                "task_status": AsyncResult(task_id, app=celery_app).status,
            }
        except ImportError:
            pass
    return {"job_id": job_id, **job}


@app.get("/ocr/result/{job_id}")
def job_result(job_id: str, tenant_id: str | None = "default") -> Response:
    """Download the OCR result for a completed job."""
    job_key = f"{tenant_id}:{job_id}"
    job = _jobs.get(job_key)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        raise HTTPException(400, f"Job is {job['status']}, not completed")

    result_path = Path("cloud_results") / f"{job_id}.md"
    if not result_path.is_file():
        raise HTTPException(404, "Result file not found")
    return Response(content=result_path.read_bytes(), media_type="text/markdown")
