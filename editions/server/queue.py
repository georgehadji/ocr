"""RQ adapter for the ``IJobQueue`` port (Server edition)."""

from __future__ import annotations

import logging
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from omniocr.domain.errors import QueueError
from omniocr.infrastructure.config import Settings
from omniocr.ports.interfaces import IJobQueue, JobState, JobStatus

from editions.common.worker import failure_reason, pages_summary

_LOG = logging.getLogger("omniocr.server.queue")

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
        return self.status(job_id).state

    def status(self, job_id: str) -> JobStatus:
        try:
            job = Job.fetch(job_id, connection=self._connection)
            raw = job.get_status()
            name = str(getattr(raw, "value", raw))
            state = _STATES.get(name)
            if state is JobState.DONE:
                total, failed = pages_summary(job.return_value())
                return JobStatus(state, pages_total=total, pages_failed=failed)
            if state is JobState.FAILED:
                if name == "failed":
                    result = job.latest_result()
                    return JobStatus(
                        state, reason=failure_reason(getattr(result, "exc_string", ""))
                    )
                return JobStatus(state, reason=f"job {name}")
        except NoSuchJobError:
            return JobStatus(JobState.NOT_FOUND)
        except RedisError as exc:
            raise QueueError(f"Redis unavailable: {exc}") from exc
        if state is None:
            _LOG.warning("unknown RQ job status %r for %s", name, job_id)
            return JobStatus(JobState.FAILED, reason=f"unknown backend status '{name}'")
        return JobStatus(state)
