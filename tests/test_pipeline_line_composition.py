"""Regression tests for line composition in ``PipelineOrchestrator._process_page``.

The defect these pin down: an engine's blocks were each turned into a
*separate candidate* for the same line, so the reconciler — whose job is to
choose between engines — chose between the words of one line and discarded
the rest. A three-line page recognized correctly by Tesseract exported as
three copies of a single word.

Blocks inside a segment belong to one line and must compose into one
candidate. Reconciliation happens across engines, never within a line.
"""

from __future__ import annotations

from typing import Sequence

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    OCRLine,
    RegionType,
    Script,
    TenantContext,
)
from omniocr.domain.result import Ok, Result
from omniocr.ports.interfaces import RawPage

CONTEXT = TenantContext(organization_id="t", user_id="u", subscription_tier="desktop")


def _run(engine_name: str) -> EngineRun:
    return EngineRun(
        engine=engine_name,
        model_ref=ModelRef(engine=engine_name, model_name="m", model_hash="h"),
        model_hash="h",
        params=(),
        timestamp="2026-08-01T00:00:00+00:00",
    )


def _block(block_id: str, text: str, x: int, engine_name: str, confidence: float) -> OCRBlock:
    return OCRBlock(
        id=block_id,
        text=text,
        confidence=Confidence(confidence),
        bbox=BBox(x=x, y=10, w=40, h=20),
        provenance=_run(engine_name),
    )


class _StubEngine:
    """Returns fixed word-level blocks, the way Tesseract does."""

    def __init__(self, name: str, blocks: Sequence[OCRBlock]) -> None:
        self.name = name
        self._blocks = tuple(blocks)
        self.calls = 0

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        self.calls += 1
        return Ok(self._blocks)


class _OneLineLayout:
    """Segments the page into a single line covering the words."""

    def segment(self, page: RawPage, context: TenantContext):
        return Ok(
            (
                OCRLine(
                    id="line-1",
                    text="",
                    confidence=Confidence(0.0),
                    bbox=BBox(0, 0, 500, 50),
                    script=Script.MODERN,
                    region_type=RegionType.MAIN_TEXT,
                    reading_order=7,
                ),
            )
        )


class _StaticRouter:
    def __init__(self, *engines: _StubEngine) -> None:
        self._engines = engines

    def route(self, line: OCRLine, context: TenantContext):
        return self._engines


class _Page:
    number = 1
    content = b"page"
    width = 500
    height = 50


def _orchestrator(*engines: _StubEngine) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        layout_analyzer=_OneLineLayout(),
        router=_StaticRouter(*engines),
    )


def test_all_words_in_a_line_survive() -> None:
    """The defect: only the highest-confidence word used to survive."""
    engine = _StubEngine(
        "tesseract",
        [
            _block("w1", "Ἡ", 0, "tesseract", 80.0),
            _block("w2", "Ἑλλάδα", 50, "tesseract", 99.0),
            _block("w3", "εἶναι", 100, "tesseract", 70.0),
        ],
    )

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)

    assert len(page.lines) == 1
    assert page.lines[0].text == "Ἡ Ἑλλάδα εἶναι"


def test_engine_emission_order_is_preserved() -> None:
    """Word order comes from the engine, not from a re-sort in this layer."""
    engine = _StubEngine(
        "tesseract",
        [
            _block("w1", "πρῶτον", 300, "tesseract", 90.0),
            _block("w2", "δεύτερον", 10, "tesseract", 90.0),
        ],
    )

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)

    assert page.lines[0].text == "πρῶτον δεύτερον"


def test_line_confidence_is_the_mean_of_its_words() -> None:
    engine = _StubEngine(
        "tesseract",
        [
            _block("w1", "α", 0, "tesseract", 100.0),
            _block("w2", "β", 50, "tesseract", 50.0),
        ],
    )

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)

    assert page.lines[0].confidence.value == 75.0


def test_line_retains_its_blocks_and_segment_metadata() -> None:
    """Blocks feed the review UI's engine comparison and word-level exports."""
    engine = _StubEngine(
        "tesseract",
        [_block("w1", "α", 0, "tesseract", 90.0), _block("w2", "β", 50, "tesseract", 90.0)],
    )

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)
    line = page.lines[0]

    assert len(line.blocks) == 2
    assert line.script is Script.MODERN
    assert line.region_type is RegionType.MAIN_TEXT
    assert line.reading_order == 7
    assert line.provenance is not None
    assert line.provenance.engine == "tesseract"


def test_line_bbox_encloses_every_block() -> None:
    engine = _StubEngine(
        "tesseract",
        [_block("w1", "α", 10, "tesseract", 90.0), _block("w2", "β", 200, "tesseract", 90.0)],
    )

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)
    bbox = page.lines[0].bbox

    assert bbox.x == 10
    assert bbox.right == 240  # 200 + 40


def test_reconciliation_happens_between_engines_not_within_a_line() -> None:
    """Two engines produce two full-line candidates; the better one wins whole."""
    from omniocr.application.reconcile import ConfidenceWeightedReconciler

    weak = _StubEngine(
        "kraken",
        [_block("k1", "λάθος", 0, "kraken", 40.0), _block("k2", "κείμενο", 50, "kraken", 40.0)],
    )
    strong = _StubEngine(
        "tesseract",
        [
            _block("t1", "σωστό", 0, "tesseract", 95.0),
            _block("t2", "κείμενο", 50, "tesseract", 95.0),
        ],
    )
    orchestrator = PipelineOrchestrator(
        layout_analyzer=_OneLineLayout(),
        router=_StaticRouter(weak, strong),
        reconciler=ConfidenceWeightedReconciler(),
    )

    page = orchestrator._process_page(_Page(), CONTEXT)

    assert page.lines[0].text == "σωστό κείμενο"
    assert page.lines[0].provenance is not None
    assert page.lines[0].provenance.engine == "tesseract"


def test_engine_is_invoked_once_per_page_not_once_per_segment() -> None:
    engine = _StubEngine("tesseract", [_block("w1", "α", 0, "tesseract", 90.0)])

    _orchestrator(engine)._process_page(_Page(), CONTEXT)

    assert engine.calls == 1


class _RecordingLogger:
    """Captures structured log calls.

    Injected rather than asserted through ``caplog`` because the pipeline
    logs via structlog when it is installed, which does not propagate to
    stdlib handlers — the assertion would then pass or fail depending on
    which optional extras happen to be present.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.records.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.records.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:
        self.records.append((event, kwargs))


def test_engine_failure_is_logged_not_swallowed() -> None:
    """A degraded ensemble must be distinguishable from a healthy one.

    Kraken failing while Tesseract succeeds still produces good-looking
    output; without this log an unattended caller cannot tell that half the
    ensemble contributed nothing.
    """

    class _FailingEngine:
        name = "kraken"

        def extract(self, page, context):
            from omniocr.domain.result import Err

            return Err(EngineError("Image is not bi-level"))

    good = _StubEngine("tesseract", [_block("w1", "κείμενο", 0, "tesseract", 90.0)])
    orchestrator = _orchestrator(_FailingEngine(), good)
    recorder = _RecordingLogger()
    orchestrator._log = recorder

    page = orchestrator._process_page(_Page(), CONTEXT)

    assert page.lines[0].text == "κείμενο", "the surviving engine still produces the line"
    failures = [fields for event, fields in recorder.records if event == "engine_failed"]
    assert len(failures) == 1, "a failing engine must be reported exactly once"
    assert failures[0]["engine"] == "kraken"
    assert "bi-level" in str(failures[0]["error"])


def test_segment_with_no_overlapping_blocks_falls_back_to_the_empty_segment() -> None:
    """A line no engine could read stays present and empty, never dropped."""
    engine = _StubEngine("tesseract", [_block("w1", "far", 5_000, "tesseract", 90.0)])

    page = _orchestrator(engine)._process_page(_Page(), CONTEXT)

    assert len(page.lines) == 1
    assert page.lines[0].text == ""
