"""Tests for the Cloud Edition composition root and Celery integration.

Full integration tests require ``celery``, ``redis``, and a running Redis
instance. The composition root import test works without external deps.
"""

from __future__ import annotations

import pytest


def test_cloud_composition_root_imports() -> None:
    """The cloud composition root module imports and constructs cleanly."""
    from editions.cloud.composition import create_cloud_pipeline

    pipeline = create_cloud_pipeline()
    assert pipeline is not None


def test_cloud_celery_app_created() -> None:
    """The Celery application is properly configured."""
    pytest.importorskip("celery")

    from editions.cloud.celery_app import app

    assert app.main == "omniocr_cloud"
    assert app.conf.broker_url == "redis://localhost:6379/0"
