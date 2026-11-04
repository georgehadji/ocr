"""Tests for GroundingGuard — VLM verification policy."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from omniocr.application.pipeline import InMemoryPage, NullRouter, PipelineOrchestrator
from omniocr.domain.errors import EngineError
from omniocr.domain.models import BBox, Confidence, OCRBlock, OCRLine, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.infrastructure.grounding import GroundedEngine, GroundingGuard


def _block(block_id: str, x: int, y: int, w: int, h: int) -> OCRBlock:
    return OCRBlock(id=block_id, text="text", confidence=Confidence(90), bbox=BBox(x, y, w, h))


def test_grounding_guard_rejects_threshold_out_of_range() -> None:
    with pytest.raises(ValueError, match="overlap_threshold"):
        GroundingGuard(overlap_threshold=-0.1)
    with pytest.raises(ValueError, match="overlap_threshold"):
        GroundingGuard(overlap_threshold=1.5)


def test_grounding_guard_accepts_overlapping_blocks() -> None:
    guard = GroundingGuard()
    vlm = [_block("vlm-1", 10, 10, 50, 50)]
    engine = [_block("eng-1", 10, 10, 50, 50)]  # exact overlap

    grounded, suggestions = guard.filter(vlm, engine)

    assert len(grounded) == 1
    assert grounded[0].id == "vlm-1"
    assert len(suggestions) == 0


def test_grounding_guard_flags_non_overlapping_blocks() -> None:
    guard = GroundingGuard()
    vlm = [_block("vlm-1", 100, 100, 10, 10)]
    engine = [_block("eng-1", 0, 0, 50, 50)]  # no overlap

    grounded, suggestions = guard.filter(vlm, engine)

    assert len(grounded) == 0
    assert len(suggestions) == 1
    assert suggestions[0].reason == "ungrounded_vlm"


def test_grounding_guard_partial_overlap_passes_zero_threshold() -> None:
    guard = GroundingGuard(overlap_threshold=0.0)
    vlm = [_block("vlm-1", 40, 40, 30, 30)]  # partial overlap
    engine = [_block("eng-1", 10, 10, 50, 50)]

    grounded, suggestions = guard.filter(vlm, engine)

    assert len(grounded) == 1


def test_grounding_guard_iou_zero_for_separate_boxes() -> None:
    """IoU of non-overlapping boxes should be 0.0."""
    guard = GroundingGuard()
    vlm = [_block("vlm-1", 200, 200, 10, 10)]
    engine = [_block("eng-1", 0, 0, 50, 50)]

    grounded, _ = guard.filter(vlm, engine)

    assert len(grounded) == 0


def test_grounding_guard_handles_empty_inputs() -> None:
    guard = GroundingGuard()

    grounded, suggestions = guard.filter([], [])

    assert len(grounded) == 0
    assert len(suggestions) == 0


class _Stub:
    """An engine returning fixed blocks, or an error."""

    def __init__(self, name: str, blocks: tuple[OCRBlock, ...] | None = None) -> None:
        self.name = name
        self._blocks = blocks

    def extract(self, page: object, context: object) -> Result[Sequence[OCRBlock], EngineError]:
        if self._blocks is None:
            return Err(EngineError("reference down"))
        return Ok(self._blocks)


class _OneLine:
    def segment(self, page: object, context: object) -> Ok:
        return Ok((OCRLine("line-1", "", Confidence(0), BBox(0, 0, 50, 20)),))


class _OnePage:
    def stream(self, document: bytes) -> Iterator[InMemoryPage]:
        yield InMemoryPage(1, b"x", 60, 30)


_CONTEXT = TenantContext("org", "user", "desktop")
_PAGE = InMemoryPage(1, b"x", 60, 30)


def test_grounded_engine_drops_blocks_over_a_region_the_reference_read_as_empty() -> None:
    ghost = _block("ghost", 0, 0, 50, 20)
    real = _block("real", 100, 100, 10, 10)
    reference = _Stub("tesseract", (_block("ref", 100, 100, 10, 10),))

    result = GroundedEngine(_Stub("vlm", (ghost, real)), reference).extract(_PAGE, _CONTEXT)

    assert isinstance(result, Ok)
    assert [b.id for b in result.value] == ["real"]


def test_grounded_engine_errs_when_the_reference_fails_rather_than_passing_blocks() -> None:
    result = GroundedEngine(_Stub("vlm", (_block("v", 0, 0, 5, 5),)), _Stub("tesseract")).extract(
        _PAGE, _CONTEXT
    )

    assert isinstance(result, Err)
    assert "cannot be grounded" in str(result.error)


def test_grounded_engine_passes_through_the_wrapped_engines_error_and_name() -> None:
    engine = GroundedEngine(_Stub("vlm"), _Stub("tesseract", ()))

    result = engine.extract(_PAGE, _CONTEXT)

    assert engine.name == "vlm"
    assert isinstance(result, Err)
    assert "reference down" in str(result.error)


def _line_text(vlm_block: OCRBlock, reference_blocks: tuple[OCRBlock, ...]) -> str:
    """Run a one-line page through the real pipeline with a grounded VLM stub."""
    grounded = GroundedEngine(_Stub("vlm", (vlm_block,)), _Stub("tesseract", reference_blocks))
    pipeline = PipelineOrchestrator(
        page_source=_OnePage(), layout_analyzer=_OneLine(), router=NullRouter(grounded)
    )
    result = pipeline.run(b"doc")
    assert isinstance(result, Ok)
    return result.value.pages[0].lines[0].text


def test_a_box_over_an_empty_region_never_reaches_the_pipeline_output() -> None:
    ghost = OCRBlock("v", "ghost", Confidence(0), BBox(0, 0, 50, 20))

    # The reference read nothing under the VLM's box, so the line stays empty.
    assert _line_text(ghost, (_block("ref", 200, 200, 10, 10),)) == ""
    # The same box over text the reference also read is kept.
    assert _line_text(ghost, (_block("ref", 0, 0, 50, 20),)) == "ghost"
