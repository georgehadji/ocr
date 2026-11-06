"""Tests for the server Edition composition root and API.

FastAPI integration tests require ``fastapi``, ``httpx``, and ``uvicorn``.
The composition root import is tested here; the shared API is tested in
``test_edition_api.py`` (requires the server extras).
"""

from __future__ import annotations


def test_server_composition_root_imports() -> None:
    """The server composition root module imports and constructs cleanly."""
    from omniocr.infrastructure.config import Settings

    from editions.server.composition import create_server_pipeline

    # A model name the manifest does not list: the checked-in models are Git LFS
    # pointers on a checkout without LFS, and the manifest hash check refuses those.
    pipeline = create_server_pipeline(Settings(kraken_model_path="not-a-real-model.mlmodel"))
    assert pipeline is not None


def test_server_pipeline_recognizes_pages_with_the_configured_workers() -> None:
    from omniocr.infrastructure.config import Settings

    from editions.server.composition import create_server_pipeline

    settings = Settings(kraken_model_path="not-a-real-model.mlmodel", max_workers=3)
    assert create_server_pipeline(settings)._max_workers == 3
    # One worker keeps the pipeline sequential, as before.
    sequential = Settings(kraken_model_path="not-a-real-model.mlmodel")
    assert create_server_pipeline(sequential)._max_workers is None


def test_server_composition_validates_upload() -> None:
    """The server pipeline's upload validation rejects invalid content."""
    from omniocr.infrastructure.security import validate_upload

    assert validate_upload(b"not-a-pdf", "book.pdf", 100).is_err()
    assert validate_upload(b"%PDF-1.7", "book.pdf", 100).is_ok()
