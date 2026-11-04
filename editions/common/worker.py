"""Edition-neutral OCR job: what an RQ or Celery worker runs for one upload."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings


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
    upload: Path, out: Path, pipeline: PipelineOrchestrator, context: TenantContext
) -> None:
    """Recognize ``upload``, write the export to ``out`` atomically, delete the upload.

    A failed run raises, so the queue records the job as failed; ``out`` is
    only ever created whole.
    """
    partial = out.with_name(out.name + ".part")
    try:
        result = pipeline.run(upload.read_bytes(), context)
        if isinstance(result, Err):
            raise RuntimeError(str(result.error))
        exported = pipeline.export(result.value, context)
        if isinstance(exported, Err):
            raise RuntimeError(str(exported.error))
        out.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(exported.value)
        os.replace(partial, out)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        upload.unlink(missing_ok=True)
