from __future__ import annotations

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


__all__ = ["RetryingEngine"]
