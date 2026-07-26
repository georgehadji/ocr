from __future__ import annotations

from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext


def _line(line_id: str, text: str, confidence: float) -> OCRLine:
    return OCRLine(
        id=line_id,
        text=text,
        confidence=Confidence(confidence),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )


def test_confidence_weighted_reconciler_selects_highest_confidence_candidate() -> None:
    lower = _line("tesseract", "ἄλλο", 72)
    higher = _line("kraken", "ἄλλος", 94)

    result = ConfidenceWeightedReconciler().reconcile(
        (lower, higher), TenantContext("org", "user", "desktop")
    )

    assert result.is_ok()
    assert result.value is higher
    assert lower.text == "ἄλλο"
    assert higher.text == "ἄλλος"


def test_confidence_weighted_reconciler_returns_error_for_no_candidates() -> None:
    result = ConfidenceWeightedReconciler().reconcile((), TenantContext("org", "user", "desktop"))

    assert result.is_err()
    assert "no OCR candidates" in str(result.error)
