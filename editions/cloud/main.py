"""OmniOCR Cloud Edition — FastAPI application with Celery backend.

Run with:

    uvicorn editions.cloud.main:app --host 0.0.0.0 --port 8000

Requires: fastapi, uvicorn, celery, redis.
Celery worker: celery -A editions.cloud.celery_app worker --loglevel=info

Job state lives in the Celery result backend, so the API can run with any
number of uvicorn workers.
"""

from __future__ import annotations

from omniocr.infrastructure.logging import configure_logging

from editions.cloud.celery_app import app as celery_app
from editions.cloud.celery_app import settings
from editions.cloud.queue import CeleryJobQueue
from editions.common.api import create_app

configure_logging("omniocr-cloud")

app = create_app("OmniOCR Cloud", CeleryJobQueue(celery_app), settings)
