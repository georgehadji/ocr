"""Edition-neutral OCR job: what an RQ or Celery worker runs for one upload."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from time import perf_counter

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.prometheus_metrics import WorkerMetrics
from omniocr.ports.interfaces import JobState

_LOG = logging.getLogger("omniocr.worker")

# A reason longer than this is cut: it goes back to an API client.
_REASON_MAX = 200


class JobFailed(RuntimeError):
    """A run that failed for a reason that is safe to show the API client.

    Every other exception a worker raises (a disk error, a bug) reaches the
    client as a generic reason, so a path or a stack frame never leaves the
    server.
    """


def failure_reason(exc_text: str | None) -> str:
    """The client-safe reason for a failed job, from its recorded exception text.

    RQ records a traceback; Celery records the message. Only a ``JobFailed``
    that is the final exception is quoted, first line only. The final exception
    starts on the first unindented line after the last indented frame line, so a
    multi-line message is found by its head, and a ``JobFailed`` that a later
    error replaced (``During handling ...``) is not quoted.
    """
    lines = [line for line in (exc_text or "").strip().splitlines() if line.strip()]
    frames = [i for i, line in enumerate(lines) if line[:1].isspace()]
    start = frames[-1] + 1 if frames else 0
    head = lines[start] if start < len(lines) else ""
    marker = f"{JobFailed.__module__}.{JobFailed.__qualname__}:"
    if head.startswith(marker):
        return head[len(marker) :].strip()[:_REASON_MAX]
    return "job failed"


def pages_summary(value: object) -> tuple[int | None, int | None]:
    """``(pages_total, pages_failed)`` from a worker's return value, or ``(None, None)``."""
    if isinstance(value, dict):
        total, failed = value.get("pages_total"), value.get("pages_failed")
        if isinstance(total, int) and isinstance(failed, int):
            return total, failed
    return None, None


def upload_exists(upload_dir: Path, job_id: str) -> bool:
    """Whether a stored upload for ``job_id`` is still on disk.

    The API writes the upload before it queues the job and the worker deletes it
    when the job ends, so a job that is queued or running always has one.
    """
    return (upload_dir / job_id).exists() or any(upload_dir.glob(f"{job_id}.*"))


def queue_id(tenant: str, job_id: str) -> str:
    """The id a job has inside the queue and on disk.

    The client sees only ``job_id``. Prefixing a hash of the tenant means one
    tenant's id resolves to nothing under another tenant's key, with no
    metadata store, and two tenants can never write the same result file.
    """
    return f"{hashlib.sha256(tenant.encode()).hexdigest()[:16]}-{job_id}"


def result_path(settings: Settings, job_id: str) -> Path:
    return settings.result_dir / f"{job_id}.md"


def run_job(
    upload: Path,
    out: Path,
    pipeline: PipelineOrchestrator,
    context: TenantContext,
    metrics: WorkerMetrics | None = None,
) -> dict[str, int]:
    """Recognize ``upload``, write the export to ``out`` atomically, delete the upload.

    A failed run, or one where every page failed, raises ``JobFailed``, so the
    queue records the job as failed; ``out`` is only ever created whole.
    ``metrics`` counts the job and the failures on its pages. Returns the page
    counts, which the queue keeps as the job's result: a run where some pages
    failed is ``done`` and the counts are how a client tells it from a full one.
    """
    partial = out.with_name(out.name + ".part")
    started = perf_counter()
    outcome = JobState.FAILED.value
    try:
        # A ``JobFailed`` message goes back to the API client, so it is built
        # from names only (an error class, a failure kind, a stage). The raw
        # text, which may carry a server path or URL, stays in the worker log.
        result = pipeline.run(upload.read_bytes(), context)
        if isinstance(result, Err):
            _LOG.warning("job failed reading %s: %s", upload.name, result.error)
            raise JobFailed(f"the document could not be read ({type(result.error).__name__})")
        # A page that fails is kept in the document with its failure, so a run
        # where every page failed still "succeeds" with nothing in it. Serving
        # that as done would hand the client an empty result.
        pages = result.value.pages
        if metrics is not None:
            metrics.record_failures(pages)
        if pages and all(page.failures for page in pages):
            first = pages[0].failures[0]
            _LOG.warning("every page failed in %s; first: %s", upload.name, first.message)
            where = f" during {first.stage}" if first.stage else ""
            raise JobFailed(f"every page failed ({len(pages)}); first: {first.error_type}{where}")
        exported = pipeline.export(result.value, context)
        if isinstance(exported, Err):
            _LOG.warning("job failed exporting %s: %s", upload.name, exported.error)
            raise JobFailed(f"export failed ({type(exported.error).__name__})")
        out.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(exported.value)
        os.replace(partial, out)
        outcome = JobState.DONE.value
        return {
            "pages_total": len(pages),
            "pages_failed": sum(1 for page in pages if page.failures),
        }
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        upload.unlink(missing_ok=True)
        if metrics is not None:
            metrics.record_job(outcome, perf_counter() - started)
