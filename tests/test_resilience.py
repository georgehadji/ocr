from omniocr.domain.errors import EngineError
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Err, Ok
from omniocr.infrastructure.resilience import RetryingEngine


class FlakyEngine:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, page, context):
        self.calls += 1
        if self.calls == 1:
            return Err(EngineError("temporary"))
        return Ok(())


def test_retrying_engine_retries_until_success() -> None:
    engine = FlakyEngine()
    result = RetryingEngine(engine, attempts=2).extract(None, TenantContext("o", "u", "d"))
    assert result.is_ok()
    assert engine.calls == 2
