"""OmniOCR Server Edition — FastAPI application.

Run with:

    uvicorn editions.server.main:app --host 0.0.0.0 --port 8000

Requires: fastapi, uvicorn, rq, redis, and the core omniocr package. Job state
lives in Redis, so the API can run with any number of uvicorn workers.
"""

from __future__ import annotations

from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.logging import configure_logging

from editions.common.api import create_app
from editions.server.queue import RqJobQueue

configure_logging("omniocr-server")

settings = Settings.from_env()
app = create_app("OmniOCR Server", RqJobQueue.from_settings(settings), settings)
