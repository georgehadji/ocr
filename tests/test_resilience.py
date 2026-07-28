from omniocr.domain.errors import EngineError
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Err, Ok
from omniocr.infrastructure.resilience import CachingEngine, CircuitBreakerEngine, RetryingEngine


class FlakyEngine:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, page, context):
        self.calls += 1
        if self.calls == 1:
            return Err(EngineError("temporary"))
        return Ok(())


class AlwaysFailEngine:
    name = "always-fail"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, page, context):
        self.calls += 1
        return Err(EngineError("downstream unavailable"))


def test_retrying_engine_retries_until_success() -> None:
    engine = FlakyEngine()
    result = RetryingEngine(engine, attempts=2).extract(None, TenantContext("o", "u", "d"))
    assert result.is_ok()
    assert engine.calls == 2


def test_circuit_breaker_opens_and_allows_a_half_open_recovery() -> None:
    engine = AlwaysFailEngine()
    now = [0.0]
    breaker = CircuitBreakerEngine(
        engine, failure_threshold=2, reset_timeout=5, clock=lambda: now[0]
    )
    context = TenantContext("o", "u", "d")

    assert breaker.extract(None, context).is_err()
    assert breaker.extract(None, context).is_ok() is False
    assert engine.calls == 2
    assert "circuit is open" in str(breaker.extract(None, context).error)
    assert engine.calls == 2

    now[0] = 5.0
    recovered = breaker.extract(None, context)

    assert recovered.is_err()
    assert engine.calls == 3


def test_caching_engine_reuses_successful_result_for_same_page_and_context() -> None:
    engine = FlakyEngine()
    cached = CachingEngine(engine)
    context = TenantContext("o", "u", "d")

    first = cached.extract(type("Page", (), {"content": b"same"})(), context)
    second = cached.extract(type("Page", (), {"content": b"same"})(), context)
    third = cached.extract(type("Page", (), {"content": b"same"})(), context)

    assert first.is_err()
    assert second.is_ok()
    assert third.is_ok()
    assert engine.calls == 2


def test_caching_engine_ttl_expiry_causes_cache_miss() -> None:
    """A TTL-based cache entry expires after the configured period."""
    engine = FlakyEngine()
    cached = CachingEngine(engine, max_size=8, ttl=1)
    context = TenantContext("o", "u", "d")
    page = type("Page", (), {"content": b"ttl-test"})()

    # First call: engine fails (calls=0→1) → cache stores nothing
    first = cached.extract(page, context)
    assert first.is_err()

    # Second call: engine succeeds (calls=1→2) → cache stores result
    second = cached.extract(page, context)
    assert second.is_ok()
    assert engine.calls == 2

    # Third call within TTL: cache hit, engine not called
    third = cached.extract(page, context)
    assert third.is_ok()
    assert engine.calls == 2  # cache hit

    # Wait for TTL to expire
    import time

    time.sleep(1.1)

    # Fourth call after TTL: cache miss, engine called again (calls=2→3 → succeeds)
    fourth = cached.extract(page, context)
    assert fourth.is_ok()
    assert engine.calls == 3
