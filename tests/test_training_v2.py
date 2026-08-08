"""Tests for domain/training.py — training value objects and type-state transitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.models import ModelRef, Script
from omniocr.domain.training import (
    EvaluationReport,
    ModelCandidate,
    PromotedModel,
    TrainingSample,
)


class TestTrainingSample:
    def test_valid_training_sample(self) -> None:
        ts = TrainingSample(
            image_path=Path("/tmp/line.png"), text="κεφάλαιον", script=Script.BYZANTINE
        )
        assert ts.text == "κεφάλαιον"
        assert ts.script == Script.BYZANTINE

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="text must not be empty"):
            TrainingSample(image_path=Path("/tmp/line.png"), text="", script=Script.MODERN)

    def test_empty_image_path_raises(self) -> None:
        with pytest.raises(ValueError, match="image_path must name a file"):
            TrainingSample(image_path=Path(""), text="text", script=Script.MODERN)


class TestModelCandidate:
    def test_valid_candidate(self, tmp_path: Path) -> None:
        path = tmp_path / "test_model.mlmodel"
        path.touch()
        mc = ModelCandidate(
            path=path,
            model_hash="a" * 64,
            parent=ModelRef(engine="kraken", model_name="base", model_hash="b" * 64),
            run_id="run-1",
        )
        assert mc.model_hash == "a" * 64

    def test_nonexistent_path_raises(self) -> None:
        with pytest.raises(ValueError, match="model path does not exist"):
            ModelCandidate(
                path=Path("/nonexistent/model.mlmodel"),
                model_hash="a" * 64,
                parent=ModelRef(engine="kraken", model_name="base", model_hash="b" * 64),
                run_id="run-1",
            )


class TestEvaluationReport:
    def test_valid_report(self) -> None:
        report = EvaluationReport(
            model_hash="a" * 64,
            per_script_cer={Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03},
            per_script_wer={Script.POLYTONIC: 0.15, Script.ANCIENT: 0.10},
            sample_count=100,
            evaluated_on="test",
        )
        assert report.sample_count == 100
        assert report.per_script_cer[Script.POLYTONIC] == 0.05

    def test_negative_cer_raises(self) -> None:
        with pytest.raises(ValueError, match="CER must not be negative"):
            EvaluationReport(
                model_hash="a" * 64,
                per_script_cer={Script.MODERN: -0.01},
                per_script_wer={Script.MODERN: 0.0},
                sample_count=10,
                evaluated_on="test",
            )


class TestTypeStateTransition:
    def test_candidate_not_promotable_directly(self, tmp_path: Path) -> None:
        """ModelCandidate cannot be used as PromotedModel without a policy decision."""
        path = tmp_path / "cand.mlmodel"
        path.touch()
        mc = ModelCandidate(
            path=path,
            model_hash="a" * 64,
            parent=ModelRef(engine="kraken", model_name="base", model_hash="b" * 64),
            run_id="run-1",
        )
        assert not isinstance(mc, PromotedModel)
