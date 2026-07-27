"""OmniOCR Server Edition — FastAPI application.

Run with:

    uvicorn editions.server.main:app --host 0.0.0.0 --port 8000

Requires: fastapi, uvicorn, rq, redis, and the core omniocr package.
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

configure_logging("omniocr-server")

app = FastAPI(title="OmniOCR Server", version="0.1.0")

# Security headers and CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"],
)

# In-memory job store for status tracking
_jobs: dict[str, dict] = {}

settings = Settings.from_env()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.post("/ocr/submit", status_code=202)
async def submit_ocr(
    file: UploadFile = File(...),
    job_id: str | None = Form(None),
):
    """Upload a PDF/image for OCR processing.

    Returns a ``job_id`` that can be used to poll ``/ocr/status/{job_id}``
    and retrieve results from ``/ocr/result/{job_id}``.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Upload is empty")

    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_bytes} byte limit")

    job_uid = job_id or str(uuid.uuid4())
    _jobs[job_uid] = {"status": "queued", "filename": file.filename}

    try:
        from rq import Queue
        from redis import Redis

        connection = Redis()
        queue = Queue("omniocr", connection=connection)
        from editions.server.composition import run_ocr_job

        queue.enqueue(run_ocr_job, data, settings)
        _jobs[job_uid] = {"status": "queued", "filename": file.filename}
    except ImportError:
        # Fallback: synchronous processing (for development without Redis)
        # In production, RQ + Redis should be available.
        _jobs[job_uid] = {"status": "processing", "filename": file.filename}
        try:
            from editions.server.composition import run_ocr_job

            result_bytes = run_ocr_job(data, settings)
            _jobs[job_uid] = {"status": "completed", "filename": file.filename}
            result_path = Path("results") / f"{job_uid}.md"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_bytes(result_bytes)
        except Exception as exc:
            _jobs[job_uid] = {"status": "failed", "error": str(exc), "filename": file.filename}

    return {"job_id": job_uid, "status": _jobs[job_uid]["status"]}


@app.get("/ocr/status/{job_id}")
def job_status(job_id: str) -> dict:
    """Poll the status of an OCR job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, **job}


@app.get("/ocr/result/{job_id}")
def job_result(job_id: str):
    """Download the OCR result for a completed job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        raise HTTPException(400, f"Job is {job['status']}, not completed")

    result_path = Path("results") / f"{job_id}.md"
    if not result_path.is_file():
        raise HTTPException(404, "Result file not found")
    return Response(content=result_path.read_bytes(), media_type="text/markdown")
