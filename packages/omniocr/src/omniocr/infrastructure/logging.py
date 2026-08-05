"""Structured logging configuration for OmniOCR.

Uses ``structlog`` with stdlib logging as the backend so logs are
compatible with existing log infrastructure while being machine-parseable
in production.
"""

from __future__ import annotations

from typing import cast

import structlog
from structlog.typing import FilteringBoundLogger


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


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Return a structlog logger bound to the calling module name."""
    import sys

    module_name = name or sys._getframe(1).f_globals.get("__name__", "omniocr")
    # structlog.get_logger() returns a BoundLoggerLazyProxy, not the final
    # bound logger — resolution to the configured wrapper_class is deferred
    # until the first actual log call, for performance. That means an
    # isinstance check here would fail even on a correctly configured
    # instance (verified: it raised on every call in testing). There is no
    # runtime-safe guard for this; structlog's own docs recommend annotating
    # via the FilteringBoundLogger protocol and casting, which is what this
    # does, rather than a guard that would be actively wrong.
    return cast(FilteringBoundLogger, structlog.get_logger(module_name))
