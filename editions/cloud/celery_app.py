"""Cloud Edition Celery application and task definitions.

The broker and result backend come from ``OMNIOCR_REDIS_URL`` (the same
setting the API uses). Tasks are discovered when the worker process starts.
"""

from __future__ import annotations

from celery import Celery

from omniocr.infrastructure.config import Settings

settings = Settings.from_env()

app = Celery(
    "omniocr_cloud",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["editions.cloud.tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_hijack_root_logger=False,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=settings.result_ttl_s,
    # With acks_late the broker redelivers a task it has not seen acknowledged
    # within visibility_timeout. Keep the task's hard limit below it, or a
    # long book is handed to a second worker while the first is still reading it.
    task_time_limit=settings.job_timeout_s,
    broker_transport_options={"visibility_timeout": settings.job_timeout_s + 3600},
)
