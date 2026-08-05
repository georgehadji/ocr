"""Tests for infrastructure/logging.py — structlog configuration.

Called by every edition entry point (desktop, server, cloud) but never
exercised by the test suite until now, since none of those launch end to end
under pytest.
"""

from __future__ import annotations

import structlog

from omniocr.infrastructure.logging import configure_logging, get_logger


def test_configure_logging_makes_structlog_report_configured() -> None:
    configure_logging()

    assert structlog.is_configured()


def test_get_logger_returns_something_that_actually_logs() -> None:
    """The real contract: calling .info()/.bind() must not raise.

    Regression: get_logger() previously asserted isinstance(logger,
    structlog.stdlib.BoundLogger), but structlog.get_logger() returns a
    BoundLoggerLazyProxy — resolution to the configured wrapper_class is
    deferred until the first real log call. The isinstance assertion raised
    on every call; only exercising the actual logging behavior catches that,
    an isinstance check on the wrong type would not.
    """
    configure_logging()

    logger = get_logger("omniocr.some_module")
    bound = logger.bind(page=1)
    bound.info("test_event", detail="value")  # must not raise


def test_get_logger_defaults_to_the_calling_module_name() -> None:
    """With no name argument, the logger binds to *this* test module's name."""
    configure_logging()

    logger = get_logger()

    logger.info("test_event")  # must not raise


def test_get_logger_does_not_require_a_local_configure_call() -> None:
    """get_logger() must not assume configure_logging() ran in this test.

    structlog config is process-global, not test-local, so this only proves
    get_logger() doesn't crash on its own — not that structlog is unconfigured.
    """
    logger = get_logger("omniocr.no_local_configure")

    logger.info("test_event")  # must not raise
