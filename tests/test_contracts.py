"""Contract tests for the IOCREngine port.

Per BUILD_PLAN §8.2: one suite per port, run against every adapter,
guaranteeing adapters are swappable.

Each engine must satisfy the same behavioral contract regardless of
implementation details.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from omniocr.domain.errors import EngineError
from omniocr.domain.models import OCRBlock, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import RawPage


def _null_page() -> RawPage:
    """A minimal page object that satisfies the RawPage protocol."""

    class _Page:
        number = 1
        content = b""
        width = 100
        height = 100

    return _Page()


def _context() -> TenantContext:
    return TenantContext("contract-test", "test", "test")


class _BrokenEngine:
    """Engine that always fails — verifies contract covers error paths."""

    name = "broken"

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        return Err(EngineError("always fails"))


# Engines to test against the contract.
_CONTRACT_ENGINES: list[tuple[str, Any, bool]] = [
    ("broken", _BrokenEngine(), False),
]

try:
    from omniocr.infrastructure.tesseract import TesseractEngine

    _CONTRACT_ENGINES.append(("tesseract", TesseractEngine("eng"), True))
except ImportError:
    pass

try:
    from omniocr.infrastructure.kraken import KrakenEngine

    _CONTRACT_ENGINES.append(("kraken", KrakenEngine(""), True))
except ImportError:
    pass

try:
    from omniocr.infrastructure.vlm import VLMEngine

    _CONTRACT_ENGINES.append(("vlm", VLMEngine("test-key"), True))
except ImportError:
    pass

try:
    from omniocr.infrastructure.calamari import CalamariEngine

    _CONTRACT_ENGINES.append(("calamari", CalamariEngine(), True))
except ImportError:
    pass

try:
    from omniocr.infrastructure.resilience import (
        RetryingEngine,
        CircuitBreakerEngine,
        CachingEngine,
    )

    _CONTRACT_ENGINES.append(("retry-wrap", RetryingEngine(_BrokenEngine()), False))
    _CONTRACT_ENGINES.append(("breaker-wrap", CircuitBreakerEngine(_BrokenEngine()), False))
    _CONTRACT_ENGINES.append(("cache-wrap", CachingEngine(_BrokenEngine()), False))
except ImportError:
    pass


@pytest.mark.parametrize("name,engine,expect_success", _CONTRACT_ENGINES)
def test_engine_has_name_attribute(name: str, engine: Any, expect_success: bool) -> None:
    """Every engine must expose a ``name`` attribute."""
    assert hasattr(engine, "name")
    assert isinstance(engine.name, str)
    assert len(engine.name) > 0


@pytest.mark.parametrize("name,engine,expect_success", _CONTRACT_ENGINES)
def test_engine_extract_returns_result(name: str, engine: Any, expect_success: bool) -> None:
    """``extract()`` must always return a ``Result`` instance."""
    result = engine.extract(_null_page(), _context())
    assert isinstance(result, (Ok, Err))


@pytest.mark.parametrize("name,engine,expect_success", _CONTRACT_ENGINES)
def test_engine_extract_result_has_correct_types(
    name: str, engine: Any, expect_success: bool
) -> None:
    """Successful results contain a sequence of OCRBlocks."""
    result = engine.extract(_null_page(), _context())

    if isinstance(result, Ok):
        for block in result.value:
            assert isinstance(block, OCRBlock)
            assert hasattr(block, "id")
            assert hasattr(block, "text")
            assert hasattr(block, "bbox")

    elif isinstance(result, Err):
        assert isinstance(result.error, EngineError)
