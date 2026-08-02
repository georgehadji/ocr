from types import SimpleNamespace

import pytest

from omniocr.domain.errors import LayoutError
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer, select_device


def test_kraken_records_become_provenanced_blocks() -> None:
    engine = KrakenEngine("missing-greek.mlmodel")
    blocks = engine.parse_records(
        [
            SimpleNamespace(
                prediction="πολυτονικό",
                confidences=[0.9, 0.8],
                line=[(2, 3), (42, 3), (42, 15), (2, 15)],
            )
        ],
        100,
        100,
    )

    assert blocks[0].text == "πολυτονικό"
    assert blocks[0].confidence.value == pytest.approx(85.0)
    assert blocks[0].bbox.right == 42
    assert blocks[0].provenance is not None
    assert blocks[0].provenance.model_hash == engine._model_hash


def test_bounds_reads_bbox_line_records() -> None:
    """Kraken 6 ``pageseg`` emits ``BBoxLine(bbox=[x0, y0, x1, y1])``.

    Regression: these were previously unhandled and fell through to a
    full-page box, which made every line cover the whole page.
    """
    record = SimpleNamespace(bbox=[31, 36, 723, 62])

    assert KrakenLayoutAnalyzer._bounds(record, 800, 180) == (31, 36, 692, 26)


def test_bounds_reads_baseline_polygon_records() -> None:
    """``blla`` emits ``BaselineLine`` with a ``boundary`` polygon."""
    record = SimpleNamespace(boundary=[(10, 20), (110, 20), (110, 50), (10, 50)])

    assert KrakenLayoutAnalyzer._bounds(record, 800, 180) == (10, 20, 100, 30)


def test_bounds_rejects_unrecognized_record_instead_of_guessing() -> None:
    """An unknown record shape must fail loudly, never default to the page box.

    A full-page fallback is silently catastrophic: every line then overlaps
    every recognized word, so each line comes back holding the whole page.
    """
    with pytest.raises(LayoutError, match="unrecognized Kraken segmentation record"):
        KrakenLayoutAnalyzer._bounds(SimpleNamespace(), 800, 180)


class TestDeviceSelection:
    """Kraken must use a GPU when one is usable and fall back to CPU otherwise."""

    def test_prefers_cuda_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
        )

        assert select_device() == "cuda"

    def test_falls_back_to_cpu_when_no_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
        )

        assert select_device() == "cpu"

    def test_falls_back_to_cpu_when_cuda_probe_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A driver mismatch raises rather than returning False — still not fatal."""

        def _boom() -> bool:
            raise RuntimeError("CUDA driver version is insufficient")

        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=_boom)),
        )

        assert select_device() == "cpu"

    def test_explicit_preference_short_circuits_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
        )

        assert select_device("cpu") == "cpu"

    def test_engine_construction_does_not_probe_the_device(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Composition roots build this engine eagerly; probing imports torch."""
        probed = False

        def _probe() -> bool:
            nonlocal probed
            probed = True
            return False

        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=_probe)),
        )

        engine = KrakenEngine("missing-greek.mlmodel")

        assert probed is False
        assert engine.device == "cpu"
        assert probed is True

    def test_provenance_records_the_device_actually_used(self) -> None:
        engine = KrakenEngine("missing-greek.mlmodel", device="cuda")
        blocks = engine.parse_records(
            [SimpleNamespace(prediction="Ἑλλάς", confidences=[0.9], line=[(0, 0), (5, 5)])],
            100,
            100,
        )

        assert blocks[0].provenance is not None
        assert blocks[0].provenance.params == ("cuda",)


def test_load_model_degrades_to_cpu_when_gpu_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GPU that cannot take the model must not fail the run."""
    attempts: list[str] = []

    def _load_any(path: str, device: str = "cpu") -> str:
        attempts.append(device)
        if device != "cpu":
            raise RuntimeError("CUDA out of memory")
        return "cpu-model"

    monkeypatch.setitem(
        __import__("sys").modules,
        "kraken.lib",
        SimpleNamespace(models=SimpleNamespace(load_any=_load_any)),
    )
    engine = KrakenEngine("missing-greek.mlmodel", device="cuda")

    assert engine._load_model() == "cpu-model"
    assert attempts == ["cuda", "cpu"]
    assert engine.device == "cpu"


def test_segment_converts_bad_records_into_err() -> None:
    """The loud failure surfaces as ``Err(LayoutError)``, not an exception."""
    import io

    from PIL import Image

    from omniocr.domain.models import TenantContext

    buffer = io.BytesIO()
    Image.new("L", (80, 40), color=255).save(buffer, format="PNG")
    page = SimpleNamespace(number=1, content=buffer.getvalue(), width=80, height=40)
    analyzer = KrakenLayoutAnalyzer(segmenter=lambda image: [SimpleNamespace()])

    result = analyzer.segment(
        page, TenantContext(organization_id="t", user_id="u", subscription_tier="desktop")
    )

    assert result.is_err()
    assert "unrecognized Kraken segmentation record" in str(result.error)
