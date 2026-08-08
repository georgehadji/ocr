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


class TestBlocksBelongToExactlyOneLine:
    """A word belongs to one line. Selecting by *any* non-zero overlap did not
    enforce that.

    Printed text lines are stacked boxes whose bounds routinely graze their
    neighbours over ascenders and descenders, so a word touching the next line
    by a pixel joined that line too. Measured on page 31 of the target
    document: 385 of 513 words (75%) were claimed by more than one line. Each
    line then emitted its own words plus slices of the lines above and below —
    the first full run produced overlapping, lossy repetitions of the same
    sentence.
    """

    @staticmethod
    def _line(line_id: str, y: int, height: int = 20) -> OCRLine:
        return OCRLine(
            id=line_id,
            text="",
            confidence=Confidence(0.0),
            bbox=BBox(x=0, y=y, w=500, h=height),
            region_type=RegionType.MAIN_TEXT,
            script=Script.POLYTONIC,
        )

    @staticmethod
    def _word(word_id: str, y: int, height: int = 18) -> OCRBlock:
        return OCRBlock(
            id=word_id,
            text=word_id,
            confidence=Confidence(90.0),
            bbox=BBox(x=10, y=y, w=40, h=height),
            provenance=_run("tesseract"),
        )

    def test_a_word_grazing_the_next_line_is_not_claimed_by_both(self) -> None:
        # Two stacked lines: 0-20 and 18-38, overlapping by 2px as real
        # segmentation does. The word sits almost entirely in the first.
        lines = [self._line("l1", y=0), self._line("l2", y=18)]
        word = self._word("w", y=1, height=18)  # 1..19 — 18px in l1, 1px in l2

        assignment = PipelineOrchestrator._assign_blocks(lines, [word])

        assert list(assignment) == [0], "the word belongs only to the line containing most of it"
        assert len(assignment[0]) == 1

    def test_no_block_is_ever_placed_twice(self) -> None:
        """The invariant, stated directly: total placements == unique blocks."""
        lines = [self._line(f"l{i}", y=i * 18) for i in range(10)]
        words = [self._word(f"w{i}", y=i * 18 + 1) for i in range(10)]

        assignment = PipelineOrchestrator._assign_blocks(lines, words)
        placed = [block for group in assignment.values() for block in group]

        assert len(placed) == len(set(id(b) for b in placed))
        assert len(placed) == len(words), "and none is lost"

    def test_each_word_lands_on_its_own_line(self) -> None:
        lines = [self._line(f"l{i}", y=i * 20) for i in range(3)]
        words = [self._word(f"w{i}", y=i * 20 + 1) for i in range(3)]

        assignment = PipelineOrchestrator._assign_blocks(lines, words)

        assert {index: [b.id for b in group] for index, group in assignment.items()} == {
            0: ["w0"],
            1: ["w1"],
            2: ["w2"],
        }

    def test_a_block_overlapping_nothing_is_dropped(self) -> None:
        lines = [self._line("l1", y=0)]
        far = self._word("w", y=5_000)

        assert PipelineOrchestrator._assign_blocks(lines, [far]) == {}

    def test_ties_keep_the_earlier_line_so_reading_order_is_stable(self) -> None:
        """Deterministic placement matters more than which line wins."""
        lines = [self._line("l1", y=0, height=20), self._line("l2", y=0, height=20)]
        word = self._word("w", y=0, height=20)

        assert list(PipelineOrchestrator._assign_blocks(lines, [word])) == [0]

    def test_overlap_area_is_zero_for_disjoint_boxes(self) -> None:
        assert PipelineOrchestrator._overlap_area(BBox(0, 0, 10, 10), BBox(50, 50, 10, 10)) == 0

    def test_overlap_area_measures_the_intersection(self) -> None:
        assert PipelineOrchestrator._overlap_area(BBox(0, 0, 10, 10), BBox(5, 5, 10, 10)) == 25
