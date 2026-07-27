"""Tests for the server Edition composition root and API.

FastAPI integration tests require ``fastapi``, ``httpx``, and ``uvicorn``.
The composition root import is tested here; full API tests are in
``test_server_api.py`` (requires server extras).
"""

from __future__ import annotations


def test_server_composition_root_imports() -> None:
    """The server composition root module imports and constructs cleanly."""
    from editions.server.composition import create_server_pipeline

    pipeline = create_server_pipeline()
    assert pipeline is not None


def test_server_composition_validates_upload() -> None:
    """The server pipeline's upload validation rejects invalid content."""
    from omniocr.infrastructure.security import validate_upload

    assert validate_upload(b"not-a-pdf", "book.pdf", 100).is_err()
    assert validate_upload(b"%PDF-1.7", "book.pdf", 100).is_ok()

