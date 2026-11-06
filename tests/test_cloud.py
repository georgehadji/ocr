"""Tests for the Cloud Edition composition root and Celery integration.

Full integration tests require ``celery``, ``redis``, and a running Redis
instance. The composition root import test works without external deps.
"""

from __future__ import annotations

import pytest


def test_cloud_composition_root_imports() -> None:
    """The cloud composition root module imports and constructs cleanly."""
    from omniocr.infrastructure.config import Settings

    from editions.cloud.composition import create_cloud_pipeline

    # A model name the manifest does not list: the checked-in models are Git LFS
    # pointers on a checkout without LFS, and the manifest hash check refuses those.
    pipeline = create_cloud_pipeline(Settings(kraken_model_path="not-a-real-model.mlmodel"))
    assert pipeline is not None


def test_cloud_pipeline_recognizes_pages_with_the_configured_workers() -> None:
    from omniocr.infrastructure.config import Settings

    from editions.cloud.composition import create_cloud_pipeline

    settings = Settings(kraken_model_path="not-a-real-model.mlmodel", max_workers=3)
    assert create_cloud_pipeline(settings)._max_workers == 3
    sequential = Settings(kraken_model_path="not-a-real-model.mlmodel")
    assert create_cloud_pipeline(sequential)._max_workers is None


def test_cloud_celery_app_created() -> None:
    """The Celery application is properly configured."""
    pytest.importorskip("celery")

    from editions.cloud.celery_app import app

    assert app.main == "omniocr_cloud"
    assert app.conf.broker_url == "redis://localhost:6379/0"
