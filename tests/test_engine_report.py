"""What `run --json` says about the engines a run used: name, device, breaker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.application.router import ScriptRouter
from omniocr.domain.errors import EngineError
from omniocr.domain.models import Script, TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.engine_report import describe_engines
from omniocr.infrastructure.kraken import KrakenEngine
from omniocr.infrastructure.resilience import CircuitBreakerEngine, RetryingEngine


class _Plain:
    name = "tesseract"


class _Down:
    name = "unlimited-ocr"

    def extract(self, page, context):
        return Err(EngineError("down"))


def test_a_plain_engine_has_no_device_and_no_breaker() -> None:
    assert describe_engines([_Plain()]) == [{"name": "tesseract", "device": None, "breaker": None}]


def test_a_kraken_engine_reports_its_device_through_its_wrappers() -> None:
    wrapped = RetryingEngine(KrakenEngine("missing.mlmodel", device="cpu"))

    assert describe_engines([wrapped]) == [{"name": "kraken", "device": "cpu", "breaker": None}]


def test_a_breaker_reports_its_state_through_other_wrappers() -> None:
    breaker = CircuitBreakerEngine(_Down(), failure_threshold=1, reset_timeout=30)
    wrapped = RetryingEngine(breaker, attempts=1)

    assert describe_engines([wrapped])[0]["breaker"] == "closed"

    wrapped.extract(None, TenantContext("o", "u", "d"))

    assert describe_engines([wrapped])[0]["breaker"] == "open"


def test_a_device_fallback_is_reported_only_when_it_happened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load_any(path: str, device: str = "cpu") -> str:
        if device != "cpu":
            raise RuntimeError("CUDA out of memory")
        return "model"

    monkeypatch.setitem(
        __import__("sys").modules,
        "kraken.lib",
        SimpleNamespace(models=SimpleNamespace(load_any=load_any)),
    )
    engine = KrakenEngine("missing.mlmodel", device="cuda")
    assert "device_fallback" not in describe_engines([engine])[0]

    engine._load_model()

    (entry,) = describe_engines([engine])
    assert entry["device"] == "cpu"
    assert entry["device_fallback"] is True


def test_the_orchestrator_lists_its_routers_engines() -> None:
    engine = _Plain()
    router = ScriptRouter({Script.ANCIENT: (engine,)}, default=(engine,))

    assert PipelineOrchestrator(router=router).engines() == (engine,)


def test_an_orchestrator_whose_router_cannot_list_engines_lists_none() -> None:
    assert PipelineOrchestrator().engines() == ()
