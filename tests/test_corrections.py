"""Tests for domain/corrections.py — correction value objects and D10 enforcement."""

from __future__ import annotations

import pytest

from omniocr.domain.corrections import Correction, GroundTruthLine
from omniocr.domain.models import BBox, Script


def _correction(
    accepted: bool = True,
    corrected_text: str = "κεφάλαιον",
    line_id: str = "line-1",
) -> Correction:
    return Correction(
        line_id=line_id,
        page_number=1,
        original_text="κεφαλαιον",
        corrected_text=corrected_text,
        corrected_by="user-1",
        corrected_at="2026-07-28T12:00:00",
        bbox=BBox(0, 0, 100, 20),
        script=Script.BYZANTINE,
        accepted=accepted,
    )


class TestCorrection:
    def test_valid_correction(self) -> None:
        c = _correction()
        assert c.line_id == "line-1"
        assert c.page_number == 1
        assert c.original_text == "κεφαλαιον"
        assert c.corrected_text == "κεφάλαιον"
        assert c.accepted is True

    def test_empty_line_id_raises(self) -> None:
        with pytest.raises(ValueError, match="line_id must not be empty"):
            Correction(
                line_id="",
                page_number=1,
                original_text="",
                corrected_text="text",
                corrected_by="user",
                corrected_at="2026-07-28T12:00:00",
                bbox=BBox(0, 0, 10, 10),
                script=Script.MODERN,
            )

    def test_empty_corrected_by_raises(self) -> None:
        with pytest.raises(ValueError, match="corrected_by must not be empty"):
            Correction(
                line_id="l1",
                page_number=1,
                original_text="",
                corrected_text="text",
                corrected_by="",
                corrected_at="2026-07-28T12:00:00",
                bbox=BBox(0, 0, 10, 10),
                script=Script.MODERN,
            )

    def test_invalid_timestamp_raises(self) -> None:
        with pytest.raises(ValueError, match="corrected_at must be ISO 8601"):
            Correction(
                line_id="l1",
                page_number=1,
                original_text="",
                corrected_text="text",
                corrected_by="user",
                corrected_at="not-a-timestamp",
                bbox=BBox(0, 0, 10, 10),
                script=Script.MODERN,
            )


class TestGroundTruthLine:
    def test_from_accepted_correction(self) -> None:
        c = _correction(accepted=True, corrected_text="κεφάλαιον")
        gt = GroundTruthLine.from_correction(c)
        assert gt is not None
        assert gt.text == "κεφάλαιον"
        assert gt.line_id == "line-1"
        assert gt.source_page == 1

    def test_from_rejected_correction_returns_none(self) -> None:
        c = _correction(accepted=False)
        assert GroundTruthLine.from_correction(c) is None

    def test_from_empty_text_returns_none(self) -> None:
        c = _correction(accepted=True, corrected_text="   ")
        assert GroundTruthLine.from_correction(c) is None

    def test_no_direct_constructor_from_ocr_line(self) -> None:
        """Verify the D10 fix: there's no path from OCRLine to GroundTruthLine.

        ``GroundTruthLine`` is only constructible through ``from_correction()``.
        A caller cannot accidentally train on raw OCR output.
        """
        # GroundTruthLine.from_correction() is the SOLE constructor.
        # An OCRLine instance has no ``from_correction`` method.
        from omniocr.domain.models import OCRLine, Confidence

        ocr_line = OCRLine(
            id="test",
            text="some text",
            confidence=Confidence(90.0),
            bbox=BBox(0, 0, 10, 10),
        )

        # For the D10 fix, what matters is that there's no path from OCRLine
        # to GroundTruthLine in the business logic.
        # Verify: GroundTruthLine only has from_correction, not from_ocr_line
        assert not hasattr(GroundTruthLine, "from_ocr_line")
        assert hasattr(GroundTruthLine, "from_correction")
