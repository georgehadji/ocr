"""Tests for GroundingGuard — VLM verification policy."""

from __future__ import annotations

import pytest

from omniocr.domain.models import BBox, Confidence, OCRBlock
from omniocr.infrastructure.grounding import GroundingGuard


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
