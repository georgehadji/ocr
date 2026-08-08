"""Structured logging configuration for OmniOCR.

Uses ``structlog`` with stdlib logging as the backend so logs are
compatible with existing log infrastructure while being machine-parseable
in production.
"""

from __future__ import annotations

import logging
import sys
from typing import IO, cast

import structlog
from structlog.typing import FilteringBoundLogger

_HANDLER_MARK = "_omniocr_handler"


def configure_logging(
    app_name: str = "omniocr",
    level: str = "INFO",
    stream: IO[str] | None = None,
) -> None:
    """Configure structlog to write diagnostics to ``stream`` (default stderr).

    Call once at a composition root before any pipeline runs.

    Two things here are load-bearing, and both were previously wrong in
    opposite directions:

    * **A handler is installed.** This routed structlog through stdlib logging
      but never gave the root logger a handler, so every edition that called
      it logged into a void — ``logging.lastResort`` drops anything below
      WARNING.
    * **The stream is stderr.** Callers that skip this function get
      structlog's own default ``PrintLogger``, which writes to **stdout**.
      That put log lines ahead of the payload in ``omniocr run --json`` and
      broke the "``--json | jq`` is safe" contract the CLI documents.

    Re-entrant: repeat calls replace this module's handler rather than
    stacking duplicates, so a Streamlit script rerunning top to bottom does
    not multiply every log line.
    """
    target = stream if stream is not None else sys.stderr

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
    handler = logging.StreamHandler(target)
    handler.setFormatter(logging.Formatter("%(message)s"))
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # colors=False: these lines are read from pipes and log files at
            # least as often as from a terminal, and ANSI escapes in a
            # captured log are noise.
            structlog.dev.ConsoleRenderer(colors=False),
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
