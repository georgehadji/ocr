import threading
from types import SimpleNamespace

import pytest

from omniocr.domain.errors import LayoutError
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer


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
    """The engine must use a GPU when one is usable and fall back to CPU otherwise.

    Selection itself is covered in ``test_device.py``; these two are about how
    ``KrakenEngine`` consumes the answer.
    """

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


class TestConcurrentModelLoad:
    """One engine instance is shared across ADR-003's page-parallel workers."""

    def test_model_is_loaded_once_under_concurrent_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Racing workers must not each load their own copy of the weights."""
        import threading

        loads = 0
        load_lock = threading.Lock()

        def _load_any(path: str, device: str = "cpu") -> str:
            nonlocal loads
            with load_lock:
                loads += 1
            return "model"

        monkeypatch.setitem(
            __import__("sys").modules,
            "kraken.lib",
            SimpleNamespace(models=SimpleNamespace(load_any=_load_any)),
        )
        engine = KrakenEngine("missing-greek.mlmodel", device="cpu")

        barrier = threading.Barrier(8)

        def _worker() -> None:
            barrier.wait()  # maximize the overlap on the lazy field
            if engine._model is None:
                with engine._lock:
                    if engine._model is None:
                        engine._model = engine._load_model()

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not any(t.is_alive() for t in threads), "deadlock: workers did not finish"
        assert loads == 1, f"model loaded {loads} times, expected exactly 1"

    def test_load_under_lock_does_not_deadlock_on_device_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: _load_model reads `device`, which re-enters the lock.

        With a non-reentrant Lock this hangs forever on the first page rather
        than failing, so assert it completes rather than trusting it returns.
        """
        monkeypatch.setitem(
            __import__("sys").modules,
            "kraken.lib",
            SimpleNamespace(models=SimpleNamespace(load_any=lambda path, device="cpu": "model")),
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "torch",
            SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
        )
        # device unresolved, so _load_model's `self.device` read re-enters.
        engine = KrakenEngine("missing-greek.mlmodel")
        assert engine._device is None

        done = threading.Event()

        def _load() -> None:
            with engine._lock:
                engine._model = engine._load_model()
            done.set()

        thread = threading.Thread(target=_load, daemon=True)
        thread.start()

        assert done.wait(timeout=10), "deadlock: _load_model blocked on the device probe"
        assert engine._model == "model"


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


class TestEngineBoundsMatchTheAnalyzer:
    """The two Kraken entry points must read geometry the same way.

    They had drifted. ``KrakenLayoutAnalyzer._bounds`` was fixed to read
    ``BBoxLine.bbox`` and to raise on an unknown shape; ``KrakenEngine._bounds``
    was not. It read ``record.line``, which Kraken 7's ``rpred`` sets to
    ``None`` on every ``BBoxOCRRecord``, so both of its fallbacks fired for
    every line on every page and stamped all 53 lines of a real page with the
    identical full-page box. Recognition was correct; the geometry was
    fabricated, and downstream the whole page collapsed onto one text line.
    """

    def test_reads_the_bbox_of_a_recognition_record(self) -> None:
        """Kraken 7 ``rpred`` yields ``BBoxOCRRecord(bbox=[x0, y0, x1, y1])``."""
        record = SimpleNamespace(bbox=[324, 102, 978, 118])

        assert KrakenEngine._bounds(record, 1339, 1890) == (324, 102, 978, 118)

    def test_a_record_without_geometry_raises_instead_of_claiming_the_page(self) -> None:
        """The defect, stated directly: `line=None` used to mean "whole page"."""
        with pytest.raises(LayoutError, match="unrecognized Kraken segmentation record"):
            KrakenEngine._bounds(SimpleNamespace(line=None), 1339, 1890)

    def test_parsed_blocks_get_distinct_boxes_not_one_page_box(self) -> None:
        """Two records on different rows must not share a bounding box."""
        engine = KrakenEngine("missing-greek.mlmodel")
        records = [
            SimpleNamespace(prediction="πρῶτος", confidences=[0.9], bbox=[10, 10, 200, 30]),
            SimpleNamespace(prediction="δεύτερος", confidences=[0.9], bbox=[10, 40, 200, 60]),
        ]

        blocks = engine.parse_records(records, 1339, 1890)

        boxes = {(b.bbox.x, b.bbox.y, b.bbox.w, b.bbox.h) for b in blocks}
        assert len(boxes) == 2, "each line keeps its own geometry"
        assert all(b.bbox.w < 1339 for b in blocks), "no block claims the full page width"
