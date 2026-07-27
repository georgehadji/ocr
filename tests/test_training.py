"""Tests for the Phase 6 training pipeline."""

from __future__ import annotations

from pathlib import Path

from omniocr.domain.models import BBox, Confidence, DocumentPage, DocumentStructure, OCRLine, RegionType, Script, Suggestion
from omniocr.infrastructure.review import ReviewDocument, build_review_document
from omniocr.infrastructure.training import compute_cer_improvement, export_ground_truth_to_kraken_json


def _review_doc() -> ReviewDocument:
    """Create a simple ReviewDocument with one page and one line."""
    run = None
    line = OCRLine(
        id="line-1",
        text="κεφάλαιον",
        confidence=Confidence(95),
        bbox=BBox(0, 0, 100, 20),
        script=Script.BYZANTINE,
        region_type=RegionType.MAIN_TEXT,
        reading_order=1,
    )
    page = DocumentPage(number=1, width=200, height=100, lines=(line,))
    structure = DocumentStructure(pages=(page,))
    return build_review_document(structure, [b"fake-image-data"])


def test_export_ground_truth_to_kraken_json(tmp_path: Path) -> None:
    """Ground-truth export produces a valid Kraken training JSON file."""
    doc = _review_doc()
    output = export_ground_truth_to_kraken_json(doc, tmp_path)

    assert output.is_file()
    import json
    records = json.loads(output.read_text())
    assert len(records) == 1
    text = records[0]["text"]
    assert len(text) > 0
    assert "page-1.png" in records[0]["image"]
    assert records[0]["image"] == "page-1.png"
    assert (tmp_path / "page-1.png").is_file()


def test_export_ground_truth_to_kraken_json_multiple_lines(tmp_path: Path) -> None:
    """Multiple lines on the same page are all written to train.json."""
    line1 = OCRLine(id="l1", text="line1", confidence=Confidence(90), bbox=BBox(0, 0, 50, 10), script=Script.BYZANTINE)
    line2 = OCRLine(id="l2", text="line2", confidence=Confidence(85), bbox=BBox(0, 20, 50, 10), script=Script.BYZANTINE)
    page = DocumentPage(number=1, width=200, height=100, lines=(line1, line2))
    structure = DocumentStructure(pages=(page,))
    doc = build_review_document(structure, [b"img"])

    output = export_ground_truth_to_kraken_json(doc, tmp_path)
    import json
    records = json.loads(output.read_text())
    assert len(records) == 2
    assert records[0]["text"] == "line1"
    assert records[1]["text"] == "line2"


def test_compute_cer_improvement_positive() -> None:
    """Improvement from 10% to 5% CER is a 50% relative improvement."""
    assert compute_cer_improvement(0.10, 0.05) == 50.0


def test_compute_cer_improvement_negative() -> None:
    """Regression from 5% to 10% CER is a -50% relative change."""
    assert compute_cer_improvement(0.05, 0.10) == -100.0


def test_compute_cer_improvement_zero_baseline() -> None:
    """Zero baseline returns 0 to avoid division by zero."""
    assert compute_cer_improvement(0.0, 0.05) == 0.0
