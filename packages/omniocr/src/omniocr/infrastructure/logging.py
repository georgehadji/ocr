"""Structured logging configuration for OmniOCR.

Uses ``structlog`` with stdlib logging as the backend so logs are
compatible with existing log infrastructure while being machine-parseable
in production.
"""

from __future__ import annotations

import structlog


def configure_logging(app_name: str = "omniocr", level: str = "INFO") -> None:
    """Configure structlog with sensible development defaults.

    Call once at the edition composition root before any pipeline runs.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the calling module name."""
    import sys

    module_name = name or sys._getframe(1).f_globals.get("__name__", "omniocr")
    logger = structlog.get_logger(module_name)
    # structlog.get_logger's return type is Any — the actual bound type
    # depends on the configured logger_factory. configure_logging() above
    # pins that to stdlib.LoggerFactory(), so this asserts a real runtime
    # invariant rather than papering over an unknown with a cast.
    assert isinstance(logger, structlog.stdlib.BoundLogger)
    return logger
