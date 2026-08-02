"""Tests for application/ground_truth.py — training data assembly (D9 fix)."""

from __future__ import annotations

from pathlib import Path


from omniocr.application.ground_truth import (
    SampleSpecification,
    assemble_training_samples,
)
from omniocr.domain.corrections import Correction
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import BBox, Script
from omniocr.domain.result import Err, Ok, Result


class _FakeCropper:
    """A cropper that returns a fixed bytes payload for any input."""

    def crop(self, page_image: bytes, bbox: BBox) -> Result[bytes, TrainingError]:
        return Ok(b"cropped-image-data")


class _FailingCropper:
    """A cropper that always fails."""

    def crop(self, page_image: bytes, bbox: BBox) -> Result[bytes, TrainingError]:
        return Err(TrainingError("crop failed"))


def _correction(
    line_id: str = "line-1",
    page: int = 1,
    text: str = "κεφάλαιον",
    accepted: bool = True,
) -> Correction:
    return Correction(
        line_id=line_id,
        page_number=page,
        original_text="original",
        corrected_text=text,
        corrected_by="tester",
        corrected_at="2026-07-28T12:00:00",
        bbox=BBox(0, 0, 100, 20),
        script=Script.BYZANTINE,
        accepted=accepted,
    )


class TestAssembleTrainingSamples:
    def test_basic_assembly(self, tmp_path: Path) -> None:
        corrections = [_correction()]
        cropper = _FakeCropper()
        page_images = {1: b"page-image-data"}
        result = assemble_training_samples(corrections, cropper, page_images, tmp_path)
        assert isinstance(result, Ok)
        samples = result.value
        assert len(samples) == 1
        assert samples[0].text == "κεφάλαιον"
        assert samples[0].image_path.exists()
        assert samples[0].image_path.read_bytes() == b"cropped-image-data"

    def test_skips_rejected_corrections(self, tmp_path: Path) -> None:
        corrections = [
            _correction(line_id="l1", text="accepted"),
            _correction(line_id="l2", text="rejected", accepted=False),
        ]
        result = assemble_training_samples(corrections, _FakeCropper(), {1: b"img"}, tmp_path)
        assert isinstance(result, Ok)
        assert len(result.value) == 1
        assert result.value[0].text == "accepted"

    def test_skips_empty_text(self, tmp_path: Path) -> None:
        corrections = [_correction(text="   ")]
        result = assemble_training_samples(corrections, _FakeCropper(), {1: b"img"}, tmp_path)
        assert isinstance(result, Ok)
        assert len(result.value) == 0

    def test_missing_page_image_returns_err(self, tmp_path: Path) -> None:
        corrections = [_correction(line_id="l1", page=99)]
        result = assemble_training_samples(corrections, _FakeCropper(), {}, tmp_path)
        assert isinstance(result, Err)

    def test_cropper_failure_propagates(self, tmp_path: Path) -> None:
        corrections = [_correction()]
        result = assemble_training_samples(corrections, _FailingCropper(), {1: b"img"}, tmp_path)
        assert isinstance(result, Err)
        assert "crop failed" in str(result.error)

    def test_script_filter(self, tmp_path: Path) -> None:
        corrections = [
            _correction(line_id="l1", text="modern", accepted=True),
        ]
        spec = SampleSpecification(allowed_scripts=(Script.ANCIENT,))
        result = assemble_training_samples(
            corrections, _FakeCropper(), {1: b"img"}, tmp_path, spec=spec
        )
        assert isinstance(result, Ok)
        assert len(result.value) == 0

    def test_d9_line_level_crops(self, tmp_path: Path) -> None:
        """D9 fix: training samples reference line crops, never page images."""
        corrections = [_correction(line_id="l1")]
        result = assemble_training_samples(corrections, _FakeCropper(), {1: b"img"}, tmp_path)
        assert isinstance(result, Ok)
        sample = result.value[0]
        # Image is a line crop, not a page
        assert sample.image_path.name.startswith("line-")
        assert "page" not in sample.image_path.name.lower()
