"""FileModelRegistry: promoted weights survive the process and recognition reads them back."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.models import ModelRef, Script
from omniocr.domain.training import EvaluationReport, PromotedModel
from omniocr.infrastructure.mlflow_registry import MlflowModelRegistry
from omniocr.infrastructure.model_manifest import resolve_kraken_model
from omniocr.infrastructure.model_registry import FileModelRegistry
from omniocr.infrastructure.models import sha256_file


def _promoted(
    tmp_path: Path, payload: bytes, script: Script = Script.POLYTONIC, name: str = "cand"
) -> PromotedModel:
    weights = tmp_path / f"{name}.mlmodel"
    weights.write_bytes(payload)
    digest = sha256_file(weights)
    return PromotedModel(
        model_ref=ModelRef(engine="kraken", model_name=str(weights), model_hash=digest),
        report=EvaluationReport(
            model_hash=digest,
            per_script_cer={script: 0.03},
            per_script_wer={script: 0.12},
            sample_count=4,
            evaluated_on="test",
        ),
        improvement_pct=23.5,
        checkpoint=weights,
    )


def test_a_registered_model_is_found_again_by_a_new_registry(tmp_path: Path) -> None:
    store = tmp_path / "promoted"
    model = _promoted(tmp_path, b"weights-1")

    assert FileModelRegistry(store).register(model).is_ok()
    found = FileModelRegistry(store).promoted_for_script(Script.POLYTONIC)

    assert found is not None
    assert found.model_ref.model_hash == model.model_ref.model_hash
    assert found.checkpoint.parent == store
    assert found.checkpoint.read_bytes() == b"weights-1"
    assert found.improvement_pct == 23.5
    assert found.report.per_script_cer == {Script.POLYTONIC: 0.03}
    assert found.report.sample_count == 4


def test_the_newest_promotion_for_a_script_wins(tmp_path: Path) -> None:
    registry = FileModelRegistry(tmp_path / "promoted")
    registry.register(_promoted(tmp_path, b"old", name="old"))
    newer = _promoted(tmp_path, b"new", name="new")
    registry.register(newer)

    found = registry.promoted_for_script(Script.POLYTONIC)

    assert found is not None and found.model_ref.model_hash == newer.model_ref.model_hash


def test_a_script_without_a_promotion_gets_none(tmp_path: Path) -> None:
    registry = FileModelRegistry(tmp_path / "promoted")
    registry.register(_promoted(tmp_path, b"w"))

    assert registry.promoted_for_script(Script.MODERN) is None
    assert FileModelRegistry(tmp_path / "empty").promoted_for_script(Script.POLYTONIC) is None


def test_weights_that_changed_after_promotion_are_not_trusted(tmp_path: Path) -> None:
    store = tmp_path / "promoted"
    registry = FileModelRegistry(store)
    registry.register(_promoted(tmp_path, b"genuine"))
    next(store.glob("*.mlmodel")).write_bytes(b"swapped")

    assert registry.promoted_for_script(Script.POLYTONIC) is None


def test_registering_weights_that_do_not_match_their_hash_is_refused(tmp_path: Path) -> None:
    model = _promoted(tmp_path, b"weights")
    model.checkpoint.write_bytes(b"changed after hashing")

    result = FileModelRegistry(tmp_path / "promoted").register(model)

    assert result.is_err()


def test_recognition_uses_the_promoted_model_for_its_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "default.mlmodel").write_bytes(b"default")
    FileModelRegistry(models / "promoted").register(_promoted(tmp_path, b"tuned"))

    chosen = resolve_kraken_model(None, models, Script.POLYTONIC)

    assert Path(chosen).parent == models / "promoted"
    assert Path(chosen).read_bytes() == b"tuned"
    assert "using promoted model" in capsys.readouterr().err


def test_an_explicit_model_beats_a_promoted_one(tmp_path: Path) -> None:
    models = tmp_path / "models"
    FileModelRegistry(models / "promoted").register(_promoted(tmp_path, b"tuned"))

    assert resolve_kraken_model("mine.mlmodel", models, Script.POLYTONIC) == "mine.mlmodel"


def test_other_scripts_and_script_less_calls_keep_the_manifest_default(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "default.mlmodel").write_bytes(b"default")
    FileModelRegistry(models / "promoted").register(_promoted(tmp_path, b"tuned"))

    assert resolve_kraken_model(None, models, Script.MODERN) == str(models / "default.mlmodel")
    assert resolve_kraken_model(None, models) == str(models / "default.mlmodel")


def test_mlflow_registry_records_into_and_reads_from_its_inner_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("omniocr.infrastructure.mlflow_registry._HAS_MLFLOW", False)
    inner = FileModelRegistry(tmp_path / "promoted")
    registry = MlflowModelRegistry(inner=inner)

    assert registry.register(_promoted(tmp_path, b"w")).is_ok()

    assert inner.promoted_for_script(Script.POLYTONIC) is not None
    assert registry.promoted_for_script(Script.POLYTONIC) is not None


def test_an_mlflow_failure_does_not_undo_a_promotion_already_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.chdir(tmp_path)
    inner = FileModelRegistry(tmp_path / "promoted")
    registry = MlflowModelRegistry(inner=inner)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("tracking server unreachable")

    monkeypatch.setattr(mlflow, "set_experiment", _boom)

    assert registry.register(_promoted(tmp_path, b"w")).is_ok()
    assert inner.promoted_for_script(Script.POLYTONIC) is not None


def test_mlflow_registry_logs_a_run_to_a_sqlite_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mlflow = pytest.importorskip("mlflow")
    # A sqlite store keeps artifacts under ./mlruns of the working directory.
    monkeypatch.chdir(tmp_path)
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    registry = MlflowModelRegistry(
        tracking_uri=uri, experiment_name="check", inner=FileModelRegistry(tmp_path / "p")
    )

    assert registry.register(_promoted(tmp_path, b"w")).is_ok()

    runs = mlflow.search_runs(experiment_names=["check"])
    assert len(runs) == 1
    assert runs["metrics.improvement_pct"].iloc[0] == 23.5
