from __future__ import annotations

from collections.abc import Callable
import hashlib
from time import monotonic

from omniocr.domain.errors import EngineError
from omniocr.domain.models import OCRBlock, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import IOCREngine, RawPage


class RetryingEngine(IOCREngine):
    """Retry a transient engine failure without coupling the engine to policy."""

    def __init__(self, engine: IOCREngine, attempts: int = 2) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        self.engine = engine
        self.attempts = attempts
        self.name = engine.name

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[tuple[OCRBlock, ...], EngineError]:
        last_error: EngineError | None = None
        for _ in range(self.attempts):
            result = self.engine.extract(page, context)
            if result.is_ok():
                return Ok(tuple(result.value))
            last_error = result.error
        return Err(last_error or EngineError(f"{self.name} failed"))


class CircuitBreakerEngine(IOCREngine):
    """Open the circuit after repeated failures and recover after a cool-down."""

    def __init__(
        self,
        engine: IOCREngine,
        failure_threshold: int = 3,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if reset_timeout <= 0:
            raise ValueError("reset_timeout must be positive")
        self.engine = engine
        self.name = engine.name
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[tuple[OCRBlock, ...], EngineError]:
        now = self._clock()
        if self._opened_at is not None:
            if now - self._opened_at < self._reset_timeout:
                return Err(EngineError(f"{self.name} circuit is open"))
            self._opened_at = None

        result = self.engine.extract(page, context)
        if result.is_ok():
            self._failures = 0
            return Ok(tuple(result.value))

        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = now
        return Err(result.error)


class CachingEngine(IOCREngine):
    """Cache successful OCR results by page bytes and relevant tenant context."""

    def __init__(self, engine: IOCREngine) -> None:
        self.engine = engine
        self.name = engine.name
        self._cache: dict[str, tuple[OCRBlock, ...]] = {}

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[tuple[OCRBlock, ...], EngineError]:
        digest = hashlib.sha256(page.content).hexdigest()
        key = "|".join(
            (
                digest,
                context.organization_id,
                context.custom_model_id or "",
            )
        )
        cached = self._cache.get(key)
        if cached is not None:
            return Ok(cached)
        result = self.engine.extract(page, context)
        if result.is_ok():
            cached_result = tuple(result.value)
            self._cache[key] = cached_result
            return Ok(cached_result)
        return Err(result.error)


__all__ = ["CachingEngine", "CircuitBreakerEngine", "RetryingEngine"]
