"""Regression tests for the pipeline logger fallback and layering.

Covers two defects found in PR review:

1. ``structlog`` is a dev-only extra, not a core dependency. The fallback
   path previously stored a bare ``logging.Logger``, but the orchestrator
   calls it with structured keyword fields (``page=``, ``duration_ms=``),
   which raises ``TypeError: Logger._log() got an unexpected keyword
   argument``. On page failures this fired even at the default WARNING
   level, so error handling crashed instead of returning a failure page.

2. ``application`` must not import concrete ``infrastructure`` adapters.
"""

from __future__ import annotations

import builtins
import logging
from typing import Any

import pytest

from omniocr.application import pipeline as pipeline_module
from omniocr.application.pipeline import (
    PipelineOrchestrator,
    _get_logger,
    _StdlibLoggerAdapter,
)


def test_stdlib_adapter_accepts_structured_keyword_fields() -> None:
    """The fallback must not raise on structlog-style kwargs."""
    adapter = _StdlibLoggerAdapter(logging.getLogger("omniocr.test.adapter"))

    adapter.info("page_completed", page=1, duration_ms=12.5)
    adapter.warning("page_failed", page=2, error="boom")
    adapter.error("pipeline_failed", error="boom")


def test_stdlib_adapter_forwards_fields_to_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structured fields land on the LogRecord rather than being dropped."""
    adapter = _StdlibLoggerAdapter(logging.getLogger("omniocr.test.record"))

    with caplog.at_level(logging.INFO, logger="omniocr.test.record"):
        adapter.info("page_completed", page=7, duration_ms=3.5)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.getMessage() == "page_completed"
    assert record.page == 7  # type: ignore[attr-defined]
    assert record.duration_ms == 3.5  # type: ignore[attr-defined]


def test_get_logger_falls_back_when_structlog_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With structlog unimportable, _get_logger returns the stdlib adapter."""
    real_import = builtins.__import__

    def _no_structlog(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "structlog":
            raise ImportError("structlog is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_structlog)

    logger = _get_logger("omniocr.test.fallback")

    assert isinstance(logger, _StdlibLoggerAdapter)


def test_pipeline_runs_and_logs_without_structlog(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A full pipeline run must succeed on a base install (no structlog).

    This is the regression: INFO-level logging with structured fields used
    to raise TypeError partway through ``run()``.
    """
    real_import = builtins.__import__

    def _no_structlog(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "structlog":
            raise ImportError("structlog is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_structlog)

    orchestrator = PipelineOrchestrator()
    assert isinstance(orchestrator._log, _StdlibLoggerAdapter)

    with caplog.at_level(logging.INFO, logger="omniocr.pipeline"):
        result = orchestrator.run(b"page-bytes")

    assert result.is_ok()
    assert any(r.getMessage() == "pipeline_start" for r in caplog.records)


def test_application_layer_does_not_import_infrastructure() -> None:
    """The pipeline module must not pull in concrete adapters (layering rule)."""
    source = pipeline_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "from omniocr.infrastructure" not in text
    assert "import omniocr.infrastructure" not in text


def test_default_exporter_produces_plain_text() -> None:
    """The application-local default exporter still exports one line per segment."""
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run(b"page-bytes")

    assert result.is_ok()
    exported = orchestrator._exporter.export(
        result.value,
        pipeline_module.TenantContext(
            organization_id="o", user_id="u", subscription_tier="desktop"
        ),
    )
    assert exported.is_ok()
    assert isinstance(exported.value, bytes)
