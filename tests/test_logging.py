"""Tests for infrastructure/logging.py — structlog configuration.

Called by every edition entry point (desktop, server, cloud) but never
exercised by the test suite until now, since none of those launch end to end
under pytest.
"""

from __future__ import annotations

import io

import pytest
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


class TestLogsActuallyReachTheStream:
    """Every test above asserts only "must not raise".

    That is exactly how a logger which silently discarded every line passed
    the suite: ``configure_logging`` routed structlog through stdlib logging
    but never installed a handler, so ``logging.lastResort`` dropped anything
    below WARNING. These assert the line *arrives*.
    """

    def test_configured_logger_writes_to_the_given_stream(self) -> None:
        stream = io.StringIO()

        configure_logging("omniocr-test", stream=stream)
        get_logger("omniocr.reaches_stream").info("landed_event", page=7)

        assert "landed_event" in stream.getvalue()
        assert "page=7" in stream.getvalue()

    def test_default_stream_is_stderr_not_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """stdout is the ``--json`` payload channel.

        A log line there put diagnostics ahead of the JSON object and broke
        ``omniocr run --json | jq``.
        """
        configure_logging("omniocr-test")
        get_logger("omniocr.stream_choice").info("diagnostic_event")

        captured = capsys.readouterr()
        assert "diagnostic_event" not in captured.out
        assert "diagnostic_event" in captured.err

    def test_repeat_configuration_does_not_duplicate_lines(self) -> None:
        """A Streamlit script reruns top to bottom on every interaction, so
        this is called again and again in one process."""
        stream = io.StringIO()

        for _ in range(3):
            configure_logging("omniocr-test", stream=stream)
        get_logger("omniocr.no_dupes").info("once_only")

        assert stream.getvalue().count("once_only") == 1

    def test_level_filters_below_threshold(self) -> None:
        stream = io.StringIO()

        configure_logging("omniocr-test", level="WARNING", stream=stream)
        logger = get_logger("omniocr.level_filter")
        logger.info("should_be_dropped")
        logger.warning("should_appear")

        assert "should_be_dropped" not in stream.getvalue()
        assert "should_appear" in stream.getvalue()
