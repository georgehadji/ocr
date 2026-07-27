from __future__ import annotations

from collections.abc import Callable, Sequence
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
    ) -> Result[Sequence[OCRBlock], EngineError]:
        last_error: EngineError | None = None
        for _ in range(self.attempts):
            result = self.engine.extract(page, context)
            if isinstance(result, Ok):
                return Ok(tuple(result.value))
            assert isinstance(result, Err)
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
    ) -> Result[Sequence[OCRBlock], EngineError]:
        now = self._clock()
        if self._opened_at is not None:
            if now - self._opened_at < self._reset_timeout:
                return Err(EngineError(f"{self.name} circuit is open"))
            self._opened_at = None

        result = self.engine.extract(page, context)
        if isinstance(result, Ok):
            self._failures = 0
            return Ok(tuple(result.value))

        assert isinstance(result, Err)
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = now
        return Err(result.error)


class CachingEngine(IOCREngine):
    """Cache successful OCR results by page bytes and relevant tenant context.

    Uses LRU eviction when the cache reaches ``max_size`` entries.
    """

    def __init__(self, engine: IOCREngine, max_size: int = 128) -> None:
        if max_size < 1:
            raise ValueError("max_size must be positive")
        self.engine = engine
        self.name = engine.name
        self._max_size = max_size
        from collections import OrderedDict
        self._cache: OrderedDict[str, tuple[OCRBlock, ...]] = OrderedDict()

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
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
            self._cache.move_to_end(key)  # LRU promotion
            return Ok(cached)
        result = self.engine.extract(page, context)
        if isinstance(result, Ok):
            cached_result = tuple(result.value)
            self._cache[key] = cached_result
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)  # evict oldest (least recently used)
            return Ok(cached_result)
        assert isinstance(result, Err)
        return Err(result.error)


__all__ = ["CachingEngine", "CircuitBreakerEngine", "RetryingEngine"]
