"""Celery adapter for the ``IJobQueue`` port (Cloud edition)."""

from __future__ import annotations

import logging
from pathlib import Path

from celery import Celery
from celery.result import AsyncResult
from kombu.exceptions import KombuError
from redis.exceptions import RedisError

from omniocr.domain.errors import QueueError
from omniocr.ports.interfaces import IJobQueue, JobState, JobStatus

from editions.common.worker import pages_summary, upload_exists

_LOG = logging.getLogger("omniocr.cloud.queue")

# Named, not imported: the API process never needs the OCR engines.
_TASK_NAME = "editions.cloud.tasks.process_ocr"

# Celery reports PENDING both for a task still waiting and for an id it has
# never heard of. When the adapter is given the upload directory it tells them
# apart (see ``status``); without it an unknown id reads as queued. Either way
# the API serves a result only once the state is DONE and the result file exists.
_STATES: dict[str, JobState] = {
    "PENDING": JobState.QUEUED,
    "RECEIVED": JobState.QUEUED,
    "STARTED": JobState.RUNNING,
    "RETRY": JobState.RUNNING,
    "SUCCESS": JobState.DONE,
    "FAILURE": JobState.FAILED,
    "REVOKED": JobState.FAILED,
    "REJECTED": JobState.FAILED,
    "IGNORED": JobState.FAILED,
}


class CeleryJobQueue(IJobQueue):
    def __init__(self, app: Celery, upload_dir: Path | None = None) -> None:
        self._app = app
        self._upload_dir = upload_dir

    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None:
        try:
            self._app.send_task(
                _TASK_NAME,
                args=[job_id, str(upload), tenant],
                task_id=job_id,
                # Fail at once when the broker is down; the default retries for
                # a long while while the caller waits on the request.
                retry=False,
            )
        except (KombuError, RedisError, OSError) as exc:
            raise QueueError(f"broker unavailable: {exc}") from exc

    def state(self, job_id: str) -> JobState:
        return self.status(job_id).state

    def status(self, job_id: str) -> JobStatus:
        try:
            result = AsyncResult(job_id, app=self._app)
            raw = result.state
            state = _STATES.get(raw)
            if state is JobState.DONE:
                total, failed = pages_summary(getattr(result, "result", None))
                return JobStatus(state, pages_total=total, pages_failed=failed)
            if state is JobState.FAILED:
                return JobStatus(
                    state, reason=_failure_reason(raw, getattr(result, "result", None))
                )
        except (KombuError, RedisError, OSError) as exc:
            raise QueueError(f"result backend unavailable: {exc}") from exc
        if state is None:
            _LOG.warning("unknown Celery task state %r for %s", raw, job_id)
            return JobStatus(JobState.FAILED, reason=f"unknown backend status '{raw}'")
        if (
            raw == "PENDING"
            and self._upload_dir is not None
            and not upload_exists(self._upload_dir, job_id)
        ):
            # Celery says PENDING for a task it has never heard of, as well as for
            # one still waiting. A waiting task always has its upload on disk, so
            # no upload means an id that was never queued, or whose result expired.
            return JobStatus(JobState.NOT_FOUND)
        return JobStatus(state)


def _failure_reason(raw: str, error: object) -> str:
    """Why a Celery task failed, in words that are safe to show a client."""
    if raw != "FAILURE":
        return f"job {raw.lower()}"
    if type(error).__name__ == "JobFailed" and str(error).strip():
        return str(error).strip().splitlines()[0][:200]
    return "job failed"
