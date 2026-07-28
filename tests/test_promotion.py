"""Tests for application/promotion.py — promotion policy rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.application.promotion import BeatsParentOnHeldOut, refuse_unless_test_split
from omniocr.domain.errors import PromotionRefused
from omniocr.domain.models import ModelRef, Script
from omniocr.domain.result import Err, Ok
from omniocr.domain.training import EvaluationReport, ModelCandidate


def _candidate(path: str = "/tmp/model.mlmodel") -> ModelCandidate:
    Path(path).touch()
    return ModelCandidate(
        path=Path(path),
        model_hash="a" * 64,
        parent=ModelRef(engine="kraken", model_name="base", model_hash="b" * 64),
        run_id="run-1",
    )


def _report(
    evaluated_on: str = "test",
    sample_count: int = 100,
    cer_values: dict | None = None,
) -> EvaluationReport:
    return EvaluationReport(
        model_hash="a" * 64,
        per_script_cer=cer_values or {Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03},
        per_script_wer={Script.POLYTONIC: 0.15, Script.ANCIENT: 0.10},
        sample_count=sample_count,
        evaluated_on=evaluated_on,
    )


class TestBeatsParentOnHeldOut:
    def setup_method(self) -> None:
        self.policy = BeatsParentOnHeldOut(min_improvement_pct=1.0, min_sample_count=10)

    def test_promote_when_candidate_beats_parent(self) -> None:
        candidate = _candidate()
        candidate_report = _report(
            cer_values={Script.POLYTONIC: 0.03, Script.ANCIENT: 0.02}
        )
        parent_report = _report(
            cer_values={Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03}
        )
        result = self.policy.decide(candidate, candidate_report, parent_report)
        assert isinstance(result, Ok)
        promoted = result.value
        assert promoted.improvement_pct > 0
        assert promoted.model_ref.model_hash == "a" * 64

    def test_refuse_wrong_split(self) -> None:
        wrong_report = _report(evaluated_on="train")
        result = self.policy.decide(_candidate(), wrong_report, _report())
        assert isinstance(result, Err)
        assert "expected 'test'" in str(result.error)

    def test_refuse_script_regression(self) -> None:
        candidate = _candidate()
        candidate_report = _report(
            cer_values={Script.POLYTONIC: 0.06, Script.ANCIENT: 0.02}
        )
        parent_report = _report(
            cer_values={Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03}
        )
        result = self.policy.decide(candidate, candidate_report, parent_report)
        assert isinstance(result, Err)
        assert "regressed" in str(result.error).lower()

    def test_refuse_below_min_sample_count(self) -> None:
        policy = BeatsParentOnHeldOut(min_sample_count=50)
        small_report = _report(sample_count=5)
        result = policy.decide(_candidate(), small_report, _report())
        assert isinstance(result, Err)
        assert "sample count" in str(result.error).lower()

    def test_refuse_below_min_improvement(self) -> None:
        policy = BeatsParentOnHeldOut(min_improvement_pct=10.0)
        candidate_report = _report(
            cer_values={Script.POLYTONIC: 0.049, Script.ANCIENT: 0.029}
        )
        parent_report = _report(
            cer_values={Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03}
        )
        result = policy.decide(_candidate(), candidate_report, parent_report)
        assert isinstance(result, Err)
        assert "below" in str(result.error).lower()

    def test_refuse_zero_parent_cer(self) -> None:
        """When parent CER is 0, any candidate error is a regression.

        The per-script regression check fires first because a parent with
        zero CER means perfect accuracy — any candidate error is a regression
        in every script.
        """
        candidate_report = _report(
            cer_values={Script.POLYTONIC: 0.01, Script.ANCIENT: 0.01}
        )
        parent_report = _report(
            cer_values={Script.POLYTONIC: 0.0, Script.ANCIENT: 0.0}
        )
        result = self.policy.decide(_candidate(), candidate_report, parent_report)
        assert isinstance(result, Err)
        # Rule 3 fires first: any positive candidate CER is a regression
        # against a parent with perfect accuracy
        assert "regressed" in str(result.error).lower()
