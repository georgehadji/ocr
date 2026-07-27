"""Cloud Edition Celery application and task definitions.

The Celery app is configured via environment variables (or defaults for
local development). Tasks are discovered automatically when the worker
process starts.
"""

from __future__ import annotations

from celery import Celery

app = Celery(
    "omniocr_cloud",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
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
)
