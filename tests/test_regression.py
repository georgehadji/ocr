"""CER/WER regression tests against the committed fixture corpus.

Each test loads a fixture's ground-truth text and a candidate hypothesis,
computes error rates via regression_exceeded(), and asserts they do not
exceed the committed baseline + tolerance.

For synthetic fixtures where the hypothesis matches ground truth, the
baseline is 0.0/0.0 and the test asserts regression_exceeded() returns False.

When real OCR engines are integrated, this test prevents accuracy
regressions on specific Greek script varieties.
"""

from __future__ import annotations

import pytest

from omniocr.application.metrics import (
    RegressionBaseline,
    regression_exceeded,
)
from omniocr.testing.fixtures import (
    load_baselines,
    load_fixture_ground_truth,
    list_fixture_ids,
)


def test_fixture_corpus_has_committed_baselines() -> None:
    """Fail early when baselines.json is missing or empty."""
    baselines = load_baselines()
    fixture_ids = list_fixture_ids()
    missing = [fid for fid in fixture_ids if fid not in baselines]
    assert not missing, f"fixtures missing baselines: {missing}"
    assert baselines, "baselines.json is empty — run scripts/compute_baselines.py"


@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_regression_gate_passes_for_perfect_recognition(fixture_id: str) -> None:
    """Assert hypothesis == ground truth does not trigger a regression flag."""
    reference = load_fixture_ground_truth(fixture_id)
    hypothesis = reference  # perfect recognition for synthetic fixtures
    baselines = load_baselines()
    entry = baselines[fixture_id]
    baseline = RegressionBaseline(cer=entry["cer"], wer=entry["wer"])
    assert not regression_exceeded(reference, hypothesis, baseline, tolerance=0.0), (
        f"{fixture_id}: perfect recognition triggered false regression"
    )


@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_regression_gate_detects_errors(fixture_id: str) -> None:
    """Assert grossly wrong hypothesis triggers the regression flag."""
    reference = load_fixture_ground_truth(fixture_id)
    hypothesis = "xxx"  # intentionally wrong
    baselines = load_baselines()
    entry = baselines[fixture_id]
    baseline = RegressionBaseline(cer=entry["cer"], wer=entry["wer"])
    assert regression_exceeded(reference, hypothesis, baseline, tolerance=0.0), (
        f"{fixture_id}: gross error was not caught by regression gate"
    )
