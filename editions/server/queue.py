"""RQ adapter for the ``IJobQueue`` port (Server edition)."""

from __future__ import annotations

from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from omniocr.domain.errors import QueueError
from omniocr.infrastructure.config import Settings
from omniocr.ports.interfaces import IJobQueue, JobState

# The worker function is named, not imported: the API process never needs the
# OCR engines, and RQ resolves the dotted path inside the worker.
_WORKER_FUNCTION = "editions.server.composition.run_ocr_job"

_STATES: dict[str, JobState] = {
    "queued": JobState.QUEUED,
    "scheduled": JobState.QUEUED,
    "deferred": JobState.QUEUED,
    "started": JobState.RUNNING,
    "finished": JobState.DONE,
    "failed": JobState.FAILED,
    "stopped": JobState.FAILED,
    "canceled": JobState.FAILED,
}


class RqJobQueue(IJobQueue):
    """Jobs live in Redis, so every API process sees the same state."""

    def __init__(
        self,
        connection: Redis,
        job_timeout_s: int,
        result_ttl_s: int,
        queue_name: str = "omniocr",
    ) -> None:
        self._connection = connection
        self._queue = Queue(queue_name, connection=connection)
        self._job_timeout_s = job_timeout_s
        self._result_ttl_s = result_ttl_s

    @classmethod
    def from_settings(cls, settings: Settings) -> RqJobQueue:
        return cls(
            Redis.from_url(settings.redis_url), settings.job_timeout_s, settings.result_ttl_s
        )

    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None:
        try:
            self._queue.enqueue(
                _WORKER_FUNCTION,
                job_id,
                str(upload),
                tenant,
                job_id=job_id,
                description=filename,
                # RQ's default is 180 s, which kills a book mid-run.
                job_timeout=self._job_timeout_s,
                result_ttl=self._result_ttl_s,
                failure_ttl=self._result_ttl_s,
            )
        except RedisError as exc:
            raise QueueError(f"Redis unavailable: {exc}") from exc

    def state(self, job_id: str) -> JobState:
        try:
            status = Job.fetch(job_id, connection=self._connection).get_status()
        except NoSuchJobError:
            return JobState.NOT_FOUND
        except RedisError as exc:
            raise QueueError(f"Redis unavailable: {exc}") from exc
        return _STATES.get(str(getattr(status, "value", status)), JobState.FAILED)
