"""HTTP layer shared by the server and cloud editions.

The two editions used to carry copies of these routes. They differ only in the
``IJobQueue`` they are built with, so the routes live here once.

Handlers are plain ``def``: FastAPI runs them in its threadpool, so copying a
large upload to disk never blocks the event loop (CLAUDE.md rule 6).
"""

from __future__ import annotations

import hmac
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePath
from typing import BinaryIO

from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from omniocr.domain.errors import QueueError
from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.prometheus_metrics import ApiMetrics, render
from omniocr.infrastructure.security import validate_upload
from omniocr.ports.interfaces import IJobQueue, JobState

from editions.common.worker import queue_id, result_path

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_CHUNK_BYTES = 1024 * 1024
# Enough to check a file's magic bytes without reading it.
_HEAD_BYTES = 16


class _TooLarge(Exception):
    pass


def _copy_limited(source: BinaryIO, dest: Path, limit: int) -> None:
    """Copy ``source`` to ``dest`` in chunks, stopping once ``limit`` bytes are exceeded."""
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := source.read(_CHUNK_BYTES):
                written += len(chunk)
                if written > limit:
                    raise _TooLarge
                out.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def _authenticator(settings: Settings) -> Callable[..., str]:
    keys = settings.api_keys

    def authenticate(authorization: str | None = Header(None)) -> str:
        """Return the tenant that owns the presented ``Authorization: Bearer`` key."""
        presented = ""
        if authorization is not None and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()
        tenant: str | None = None
        # Compare against every key, never stopping at the first match, so the
        # time taken does not say which key was close.
        for key, owner in keys:
            if hmac.compare_digest(key.encode(), presented.encode()):
                tenant = owner
        if tenant is None:
            raise HTTPException(
                401, "Missing or invalid API key", headers={"WWW-Authenticate": "Bearer"}
            )
        return tenant

    return authenticate


def build_router(queue: IJobQueue, settings: Settings, metrics: ApiMetrics) -> APIRouter:
    router = APIRouter()
    authenticate = _authenticator(settings)

    @router.get("/metrics")
    def scrape(_tenant: str = Depends(authenticate)) -> Response:
        """Prometheus text for the API and, when the metrics volume is shared, the workers.

        Needs a valid key (Prometheus: ``authorization`` with ``type: Bearer``). Carries no
        tenant labels, so any tenant's key reveals nothing about another's jobs.
        """
        return Response(content=render(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    def current_state(tenant: str, job_id: str) -> JobState:
        if not _JOB_ID.match(job_id):
            raise HTTPException(404, "Job not found")
        try:
            state = queue.state(queue_id(tenant, job_id))
        except QueueError as exc:
            raise HTTPException(503, "Job queue unavailable") from exc
        if state is JobState.NOT_FOUND:
            raise HTTPException(404, "Job not found")
        return state

    @router.post("/ocr/submit", status_code=202)
    def submit_ocr(
        file: UploadFile = File(...), tenant: str = Depends(authenticate)
    ) -> dict[str, str]:
        """Store an upload and queue it. Poll ``/ocr/status/{job_id}`` for progress."""
        filename = file.filename or ""
        head = file.file.read(_HEAD_BYTES)
        file.file.seek(0)
        checked = validate_upload(head, filename, max(settings.max_upload_bytes, len(head)))
        if isinstance(checked, Err):
            raise HTTPException(400, str(checked.error))

        job_id = uuid.uuid4().hex
        internal_id = queue_id(tenant, job_id)
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        upload = settings.upload_dir / f"{internal_id}{PurePath(filename).suffix.lower()}"
        try:
            _copy_limited(file.file, upload, settings.max_upload_bytes)
        except _TooLarge:
            raise HTTPException(
                413, f"Upload exceeds {settings.max_upload_bytes} byte limit"
            ) from None

        try:
            queue.submit(internal_id, upload, filename, tenant)
        except QueueError as exc:
            # Nothing was queued, so nothing will ever delete this file.
            upload.unlink(missing_ok=True)
            raise HTTPException(503, "Job queue unavailable") from exc
        metrics.jobs_submitted.inc()
        return {"job_id": job_id, "status": JobState.QUEUED.value}

    @router.get("/ocr/status/{job_id}")
    def job_status(job_id: str, tenant: str = Depends(authenticate)) -> dict[str, str]:
        return {"job_id": job_id, "status": current_state(tenant, job_id).value}

    @router.get("/ocr/result/{job_id}")
    def job_result(job_id: str, tenant: str = Depends(authenticate)) -> Response:
        state = current_state(tenant, job_id)
        if state is not JobState.DONE:
            raise HTTPException(400, f"Job is {state.value}, not completed")
        path = result_path(settings, queue_id(tenant, job_id))
        if not path.is_file():
            raise HTTPException(404, "Result file not found")
        return Response(content=path.read_bytes(), media_type="text/markdown")

    return router


def create_app(title: str, queue: IJobQueue, settings: Settings) -> FastAPI:
    """The FastAPI app for an edition: its queue, plus same-origin-by-default security."""
    app = FastAPI(title=title, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["POST", "GET"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    metrics = ApiMetrics()

    @app.middleware("http")
    async def count_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # The route template, never the URL: a job id in the label would make a series per job.
        route = request.scope.get("route")
        metrics.requests.labels(
            method=request.method,
            route=route.path if route is not None else "unmatched",
            status=str(response.status_code),
        ).inc()
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name}

    app.include_router(build_router(queue, settings, metrics))
    return app
