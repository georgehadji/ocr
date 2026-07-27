"""OmniOCR Server Edition — RQ worker entrypoint.

Run with:

    rq worker --url redis://localhost:6379 omniocr

The worker picks up OCR jobs enqueued by the FastAPI application
and processes them using the server composition root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repository root is on sys.path so imports resolve.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Import the worker function so RQ can discover it.
from editions.server.composition import run_ocr_job  # noqa: F401
