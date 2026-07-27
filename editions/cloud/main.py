"""OmniOCR Cloud Edition — FastAPI application with Celery backend.

Run with:

    uvicorn editions.cloud.main:app --host 0.0.0.0 --port 8000

Requires: fastapi, uvicorn, celery, redis.
Celery worker: celery -A editions.cloud.celery_app worker --loglevel=info
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

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
_jobs: dict[str, dict] = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "tenant": "multi"}


@app.post("/ocr/submit", status_code=202)
async def submit_ocr(
    file: UploadFile = File(...),
    job_id: str | None = Form(None),
    tenant_id: str | None = Form("default"),
):
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

        task = process_ocr.delay(data, settings)
        _jobs[job_key] = {
            "status": "queued",
            "filename": file.filename,
            "tenant_id": tenant_id,
            "task_id": task.id,
        }
    except ImportError:
        # Fallback: synchronous processing (for development without Celery)
        from editions.cloud.composition import create_cloud_pipeline

        pipeline = create_cloud_pipeline(settings)
        from omniocr.domain.models import TenantContext

        ctx = TenantContext(organization_id=tenant_id or "default", user_id="cloud", subscription_tier="cloud")
        run_result = pipeline.run(data, ctx)
        if run_result.is_err():
            _jobs[job_key] = {"status": "failed", "error": str(run_result.error)}
            return {"job_id": job_uid, "status": "failed"}

        export_result = pipeline.export(run_result.value, ctx)
        result_bytes = export_result.value if export_result.is_ok() else b""

        result_path = Path("cloud_results") / f"{job_uid}.md"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_bytes(result_bytes)
        _jobs[job_key] = {"status": "completed", "filename": file.filename, "tenant_id": tenant_id}

    return {"job_id": job_uid, "status": _jobs[job_key]["status"]}


@app.get("/ocr/status/{job_id}")
def job_status(job_id: str, tenant_id: str | None = "default") -> dict:
    """Poll the status of an OCR job (scoped to tenant)."""
    job_key = f"{tenant_id}:{job_id}"
    job = _jobs.get(job_key)
    if job is None:
        # Check Celery result backend
        try:
            from celery.result import AsyncResult
            from editions.cloud.celery_app import app as celery_app

            task_id = next(
                (j["task_id"] for j in _jobs.values() if j.get("task_id")),
                None,
            )
            if task_id:
                async_result = AsyncResult(task_id, app=celery_app)
                return {"job_id": job_id, "task_status": async_result.status, "tenant_id": tenant_id}
        except ImportError:
            pass
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@app.get("/ocr/result/{job_id}")
def job_result(job_id: str, tenant_id: str | None = "default"):
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
