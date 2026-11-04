"""Celery adapter for the ``IJobQueue`` port (Cloud edition)."""

from __future__ import annotations

from pathlib import Path

from celery import Celery
from celery.result import AsyncResult
from kombu.exceptions import KombuError
from redis.exceptions import RedisError

from omniocr.domain.errors import QueueError
from omniocr.ports.interfaces import IJobQueue, JobState

# Named, not imported: the API process never needs the OCR engines.
_TASK_NAME = "editions.cloud.tasks.process_ocr"

# Celery reports PENDING both for a task still waiting and for an id it has
# never heard of, so an unknown id reads as queued rather than as not found.
# The API only ever asks about ids carrying the caller's own tenant prefix, and
# serves a result only once the state is DONE and the result file exists.
_STATES: dict[str, JobState] = {
    "PENDING": JobState.QUEUED,
    "RECEIVED": JobState.QUEUED,
    "STARTED": JobState.RUNNING,
    "RETRY": JobState.RUNNING,
    "SUCCESS": JobState.DONE,
    "FAILURE": JobState.FAILED,
    "REVOKED": JobState.FAILED,
}


class CeleryJobQueue(IJobQueue):
    def __init__(self, app: Celery) -> None:
        self._app = app

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
        try:
            status = AsyncResult(job_id, app=self._app).state
        except (KombuError, RedisError, OSError) as exc:
            raise QueueError(f"result backend unavailable: {exc}") from exc
        return _STATES.get(status, JobState.RUNNING)
