"""A run's engines as plain data, for ``omniocr run --json``.

Engines arrive wrapped (retry, grounding, circuit breaker), each wrapper
holding the next in ``.engine``. The report looks through them, so a breaker
buried under a retrying wrapper is still seen.
"""

from __future__ import annotations

from collections.abc import Iterable

from omniocr.ports.interfaces import IOCREngine

from omniocr.infrastructure.kraken import KrakenEngine
from omniocr.infrastructure.resilience import CircuitBreakerEngine


def _layers(engine: IOCREngine) -> list[object]:
    """``engine`` and everything it wraps, outermost first."""
    chain: list[object] = []
    current: object | None = engine
    while current is not None and not any(current is seen for seen in chain):
        chain.append(current)
        current = getattr(current, "engine", None)
    return chain


def describe_engines(engines: Iterable[IOCREngine]) -> list[dict[str, str | bool | None]]:
    """``name``, ``device`` and ``breaker`` per engine; ``device_fallback`` only when it happened.

    ``device`` and ``breaker`` are ``None`` for an engine that has neither.
    """
    report: list[dict[str, str | bool | None]] = []
    for engine in engines:
        layers = _layers(engine)
        kraken = next((layer for layer in layers if isinstance(layer, KrakenEngine)), None)
        breaker = next((layer for layer in layers if isinstance(layer, CircuitBreakerEngine)), None)
        entry: dict[str, str | bool | None] = {
            "name": engine.name,
            "device": kraken.device if kraken is not None else None,
            "breaker": breaker.state if breaker is not None else None,
        }
        if kraken is not None and kraken.device_fallback:
            entry["device_fallback"] = True
        report.append(entry)
    return report


__all__ = ["describe_engines"]
