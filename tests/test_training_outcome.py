"""Training says what happened: outcomes, refusal reasons, and a registry that never overwrites a
damaged index (Phase 4)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from test_model_registry import _promoted
from test_promotion import _candidate, _report
from test_training_orchestrator import (
    _Cropper,
    _Exporter,
    _Store,
    _Trainer,
    _correction,
)

from omniocr.application.promotion import BeatsParentOnHeldOut
from omniocr.application.training_orchestrator import TrainingOrchestrator
from omniocr.domain.errors import PromotionRefused, RefusalReason
from omniocr.domain.models import BBox, EventType, ModelRef, Script
from omniocr.domain.result import Err, Ok
from omniocr.domain.training import EvaluationReport, TrainingOutcome, TrainingReport
from omniocr.infrastructure.ketos_trainer import KetosTrainer
from omniocr.infrastructure.mlflow_registry import InMemoryModelRegistry
from omniocr.infrastructure.model_registry import INDEX_FILENAME, FileModelRegistry

# --- the strings are a contract ---------------------------------------------


def test_outcomes_and_reasons_have_stable_wire_strings() -> None:
    assert [str(o) for o in TrainingOutcome] == [
        "no_corrections",
        "no_samples",
        "refused",
        "promoted",
    ]
    assert [str(r) for r in RefusalReason] == [
        "wrong_split",
        "too_few_samples",
        "script_regressed",
        "below_threshold",
        "parent_cer_zero",
    ]


def test_training_event_types_exist() -> None:
    assert {"training_refused", "training_no_corrections", "training_no_samples"} <= {
        str(e) for e in EventType
    }


# --- the report cannot contradict itself ------------------------------------


def _refusal() -> PromotionRefused:
    return PromotionRefused("no", RefusalReason.BELOW_THRESHOLD)


def test_a_promoted_report_carries_the_model(tmp_path: Path) -> None:
    model = _promoted(tmp_path, b"w")

    report = TrainingReport(TrainingOutcome.PROMOTED, promoted=model)

    assert report.promoted is model and report.refusal is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outcome": TrainingOutcome.PROMOTED},  # promoted but no model
        {"outcome": TrainingOutcome.REFUSED},  # refused but no refusal
        {"outcome": TrainingOutcome.NO_SAMPLES, "refusal": _refusal()},
        {"outcome": TrainingOutcome.NO_CORRECTIONS, "refusal": _refusal()},
    ],
)
def test_a_report_that_contradicts_its_outcome_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        TrainingReport(**kwargs)


def test_a_model_with_a_refusal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        TrainingReport(
            TrainingOutcome.PROMOTED, promoted=_promoted(tmp_path, b"w"), refusal=_refusal()
        )


def test_a_model_on_a_refused_report_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        TrainingReport(
            TrainingOutcome.REFUSED, promoted=_promoted(tmp_path, b"w"), refusal=_refusal()
        )


# --- the policy says why it refused ----------------------------------------


def _reason(tmp_path: Path, candidate: EvaluationReport, parent: EvaluationReport, **kw):
    policy = BeatsParentOnHeldOut(**({"min_improvement_pct": 1.0, "min_sample_count": 10} | kw))
    result = policy.decide(_candidate(tmp_path / "m.mlmodel"), candidate, parent)
    assert isinstance(result, Err)
    return result.error.reason


def test_refusal_reasons(tmp_path: Path) -> None:
    ok = {Script.POLYTONIC: 0.03, Script.ANCIENT: 0.02}
    parent = _report(cer_values={Script.POLYTONIC: 0.05, Script.ANCIENT: 0.03})

    assert (
        _reason(tmp_path, _report(evaluated_on="train", cer_values=ok), parent)
        is RefusalReason.WRONG_SPLIT
    )
    assert (
        _reason(tmp_path, _report(sample_count=2, cer_values=ok), parent)
        is RefusalReason.TOO_FEW_SAMPLES
    )
    assert (
        _reason(
            tmp_path,
            _report(cer_values={Script.POLYTONIC: 0.01, Script.ANCIENT: 0.04}),
            parent,
        )
        is RefusalReason.SCRIPT_REGRESSED
    )
    assert (
        _reason(
            tmp_path,
            _report(cer_values={Script.POLYTONIC: 0.0499, Script.ANCIENT: 0.0299}),
            parent,
        )
        is RefusalReason.BELOW_THRESHOLD
    )
    assert (
        _reason(
            tmp_path,
            _report(cer_values={Script.POLYTONIC: 0.0}),
            _report(cer_values={Script.POLYTONIC: 0.0}),
        )
        is RefusalReason.PARENT_CER_ZERO
    )


def test_a_refusal_built_the_old_way_has_no_reason() -> None:
    assert PromotionRefused("plain").reason is None
    assert str(PromotionRefused("plain")) == "plain"


# --- the orchestrator reports every outcome ---------------------------------


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


class _Evaluator:
    def __init__(self, candidate_cer: float, parent_cer: float) -> None:
        self._cers = {"candidate": candidate_cer, "parent": parent_cer}

    def evaluate(self, engine, split):
        cer = self._cers["candidate" if "candidate" in engine.name else "parent"]
        return Ok(
            EvaluationReport(
                model_hash="a" * 64,
                per_script_cer={Script.MODERN: cer},
                per_script_wer={Script.MODERN: cer},
                sample_count=5,
                evaluated_on="test",
            )
        )


class _NamedEngine:
    def __init__(self, path: Path) -> None:
        self.name = path.name

    def extract(self, page, context):  # pragma: no cover - the evaluator never calls it
        return Ok(())


def _run(tmp_path: Path, *, corrections=None, cers=(0.01, 0.05), bus=None):
    orchestrator = TrainingOrchestrator(
        correction_store=_Store([_correction()] if corrections is None else corrections),
        cropper=_Cropper(),
        exporter=_Exporter(),
        trainer=_Trainer(tmp_path),
        evaluator=_Evaluator(*cers),
        promotion_policy=BeatsParentOnHeldOut(min_improvement_pct=1.0, min_sample_count=1),
        model_registry=InMemoryModelRegistry(),
        engine_factory=_NamedEngine,
        event_bus=bus,
    )
    parent = ModelRef("kraken", str(tmp_path / "parent.mlmodel"), "p" * 64)
    return orchestrator.run_training(parent, {1: b"page"}, tmp_path / "out")


def test_no_corrections_is_an_outcome_not_a_silent_none(tmp_path: Path) -> None:
    bus = _Bus()

    result = _run(tmp_path, corrections=[], bus=bus)

    assert isinstance(result, Ok)
    assert result.value.outcome is TrainingOutcome.NO_CORRECTIONS
    assert [e.event_type for e in bus.events] == [EventType.TRAINING_NO_CORRECTIONS]


def test_corrections_that_make_no_sample_are_an_outcome(tmp_path: Path) -> None:
    short = replace(_correction(), bbox=BBox(0, 0, 100, 2))
    bus = _Bus()

    result = _run(tmp_path, corrections=[short], bus=bus)

    assert result.value.outcome is TrainingOutcome.NO_SAMPLES
    assert [e.event_type for e in bus.events] == [EventType.TRAINING_NO_SAMPLES]


def test_a_refused_candidate_says_why(tmp_path: Path) -> None:
    bus = _Bus()

    result = _run(tmp_path, cers=(0.0499, 0.05), bus=bus)

    report = result.value
    assert report.outcome is TrainingOutcome.REFUSED
    assert report.promoted is None
    assert report.refusal.reason is RefusalReason.BELOW_THRESHOLD
    assert [e.event_type for e in bus.events] == [EventType.TRAINING_REFUSED]
    assert bus.events[0].detail == str(report.refusal)


def test_a_promoted_candidate_is_reported_and_announced(tmp_path: Path) -> None:
    bus = _Bus()

    result = _run(tmp_path, cers=(0.01, 0.05), bus=bus)

    report = result.value
    assert report.outcome is TrainingOutcome.PROMOTED
    assert report.promoted is not None and report.refusal is None
    assert [e.event_type for e in bus.events] == [EventType.MODEL_PROMOTED]
    assert bus.events[0].model_hash == report.promoted.model_ref.model_hash


def test_no_events_are_needed(tmp_path: Path) -> None:
    assert _run(tmp_path, cers=(0.01, 0.05), bus=None).value.outcome is TrainingOutcome.PROMOTED


# --- a damaged index is never overwritten -----------------------------------


def _index(directory: Path) -> Path:
    return directory / INDEX_FILENAME


@pytest.mark.parametrize("damage", ["{ not json", "", '{"a": 1}', '[{"ok": 1}, 7]'])
def test_registering_over_a_damaged_index_is_refused_and_leaves_it_alone(
    tmp_path: Path, damage: str
) -> None:
    registry_dir = tmp_path / "reg"
    registry_dir.mkdir()
    _index(registry_dir).write_text(damage, encoding="utf-8")

    result = FileModelRegistry(registry_dir).register(_promoted(tmp_path, b"weights"))

    assert result.is_err()
    assert "promoted.json" in str(result.error)
    assert _index(registry_dir).read_text(encoding="utf-8") == damage


def test_a_missing_index_is_a_new_registry_not_damage(tmp_path: Path) -> None:
    registry_dir = tmp_path / "reg"

    assert FileModelRegistry(registry_dir).register(_promoted(tmp_path, b"weights")).is_ok()
    assert len(json.loads(_index(registry_dir).read_text(encoding="utf-8"))) == 1


def test_reading_a_damaged_index_finds_no_model_instead_of_raising(tmp_path: Path) -> None:
    registry_dir = tmp_path / "reg"
    registry_dir.mkdir()
    _index(registry_dir).write_text("{ not json", encoding="utf-8")

    assert FileModelRegistry(registry_dir).promoted_for_script(Script.POLYTONIC) is None


# --- a stale checkpoint is never picked up ----------------------------------


def _data(tmp_path: Path) -> Path:
    data = tmp_path / "training_data"
    (data / "images").mkdir(parents=True)
    (data / "train.json").write_text("{}", encoding="utf-8")
    (data / "images" / "line-1.png").write_bytes(b"png")
    return data


def test_a_checkpoint_left_by_an_earlier_run_is_not_this_runs_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = _data(tmp_path)
    parent = tmp_path / "parent.mlmodel"
    parent.write_bytes(b"parent")
    stale = tmp_path / "ketos_out"
    stale.mkdir()
    (stale / "best_0.9999.mlmodel").write_bytes(b"from an earlier run")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )

    result = KetosTrainer().train(data, ModelRef("kraken", str(parent), "p" * 64), {})

    assert result.is_err()
    assert "did not produce output model" in str(result.error)
