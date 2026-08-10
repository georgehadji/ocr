"""FallbackLayoutAnalyzer: zero lines must be treated as failure, not as a blank page."""

from __future__ import annotations

from typing import Sequence

from omniocr.application.layout import FallbackLayoutAnalyzer
from omniocr.domain.errors import LayoutError
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext
from omniocr.domain.result import Err, Ok, Result

CONTEXT = TenantContext(organization_id="t", user_id="t", subscription_tier="desktop")


class Stub:
    """Layout analyzer returning a canned result."""

    def __init__(self, result: Result[Sequence[OCRLine], LayoutError]) -> None:
        self.result = result
        self.calls = 0

    def segment(self, page: object, context: TenantContext) -> Result[Sequence[OCRLine], LayoutError]:
        self.calls += 1
        return self.result


def line(identifier: str) -> OCRLine:
    return OCRLine(
        id=identifier,
        text="",
        confidence=Confidence(0.0),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )


def test_primary_result_is_used_when_it_finds_lines() -> None:
    primary = Stub(Ok((line("a"),)))
    fallback = Stub(Ok((line("b"),)))

    result = FallbackLayoutAnalyzer(primary, fallback).segment(object(), CONTEXT)

    assert isinstance(result, Ok)
    assert result.value[0].id == "a"
    assert fallback.calls == 0


def test_empty_primary_falls_back() -> None:
    """The real bug: Kraken returns Ok(()) with no error and the page vanishes."""
    primary = Stub(Ok(()))
    fallback = Stub(Ok((line("b"),)))

    result = FallbackLayoutAnalyzer(primary, fallback).segment(object(), CONTEXT)

    assert isinstance(result, Ok)
    assert result.value[0].id == "b"
    assert fallback.calls == 1


def test_errored_primary_falls_back() -> None:
    primary = Stub(Err(LayoutError("kraken exploded")))
    fallback = Stub(Ok((line("b"),)))

    result = FallbackLayoutAnalyzer(primary, fallback).segment(object(), CONTEXT)

    assert isinstance(result, Ok)
    assert result.value[0].id == "b"


def test_primary_error_is_reported_when_both_fail() -> None:
    primary = Stub(Err(LayoutError("kraken exploded")))
    fallback = Stub(Ok(()))

    result = FallbackLayoutAnalyzer(primary, fallback).segment(object(), CONTEXT)

    assert isinstance(result, Err)
    assert "kraken" in str(result.error)


def test_both_empty_returns_empty() -> None:
    result = FallbackLayoutAnalyzer(Stub(Ok(())), Stub(Ok(()))).segment(object(), CONTEXT)

    assert isinstance(result, Ok)
    assert result.value == ()
