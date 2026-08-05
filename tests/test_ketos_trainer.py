"""Tests for KetosTrainer — subprocess adapter over the Kraken CLI.

The Kraken CLI itself is not invoked: an actual training run needs real
training data and can run for hours, unsuitable for a unit test. subprocess.run
is monkeypatched at the module boundary, same as any other OS-process seam.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from omniocr.domain.models import ModelRef
from omniocr.infrastructure.ketos_trainer import KetosTrainer


def _parent_ref(parent_path: Path) -> ModelRef:
    return ModelRef(engine="kraken", model_name=str(parent_path), model_hash="deadbeef")


def _make_parent_model(tmp_path: Path) -> Path:
    parent_path = tmp_path / "parent.mlmodel"
    parent_path.write_bytes(b"fake model bytes")
    return parent_path


def _make_train_data(tmp_path: Path) -> Path:
    data_dir = tmp_path / "training_data"
    data_dir.mkdir()
    (data_dir / "train.json").write_text("{}", encoding="utf-8")
    return data_dir


def test_train_fails_fast_when_train_json_missing(tmp_path: Path) -> None:
    trainer = KetosTrainer()
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = trainer.train(empty_dir, _parent_ref(tmp_path / "parent.mlmodel"), {})

    assert result.is_err()
    assert "training data not found" in str(result.error)


def test_train_fails_fast_when_parent_model_missing(tmp_path: Path) -> None:
    trainer = KetosTrainer()
    data_dir = _make_train_data(tmp_path)

    result = trainer.train(data_dir, _parent_ref(tmp_path / "does_not_exist.mlmodel"), {})

    assert result.is_err()
    assert "parent model not found" in str(result.error)


def test_train_returns_err_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="kraken", timeout=1)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    trainer = KetosTrainer(timeout_seconds=1)

    result = trainer.train(data_dir, _parent_ref(parent_path), {})

    assert result.is_err()
    assert "timed out after 1s" in str(result.error)


def test_train_returns_err_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="ketos: CUDA out of memory"
        ),
    )
    trainer = KetosTrainer()

    result = trainer.train(data_dir, _parent_ref(parent_path), {})

    assert result.is_err()
    assert "exit 1" in str(result.error)
    assert "CUDA out of memory" in str(result.error)


def test_train_returns_err_when_output_model_not_produced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A zero exit code alone isn't success — the artifact must actually exist."""
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    trainer = KetosTrainer()

    result = trainer.train(data_dir, _parent_ref(parent_path), {})

    assert result.is_err()
    assert "did not produce output model" in str(result.error)


def test_train_succeeds_and_returns_candidate_with_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)
    expected_output = data_dir.parent / "ketos_finetuned.mlmodel"

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        # Simulate ketos producing its output artifact as a side effect.
        expected_output.write_bytes(b"fine-tuned model bytes")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    trainer = KetosTrainer()

    result = trainer.train(data_dir, _parent_ref(parent_path), {"epochs": "5", "run_id": "run-42"})

    assert result.is_ok()
    candidate = result.value
    assert candidate.path == expected_output
    assert candidate.run_id == "run-42"
    assert candidate.parent.model_name == str(parent_path)
    assert len(candidate.model_hash) == 64  # sha256 hex digest


def test_train_includes_optional_hyperparameters_in_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)
    expected_output = data_dir.parent / "ketos_finetuned.mlmodel"
    captured_cmd: list[str] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured_cmd.extend(cmd)
        expected_output.write_bytes(b"model")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    trainer = KetosTrainer(device="cuda")

    result = trainer.train(
        data_dir,
        _parent_ref(parent_path),
        {"learning_rate": "0.001", "batch_size": "8", "decay": "0.9", "momentum": "0.99"},
    )

    assert result.is_ok()
    assert "--learning-rate" in captured_cmd and "0.001" in captured_cmd
    assert "--batch-size" in captured_cmd and "8" in captured_cmd
    assert "--decay" in captured_cmd and "0.9" in captured_cmd
    assert "--momentum" in captured_cmd and "0.99" in captured_cmd
    assert "--device" in captured_cmd and "cuda" in captured_cmd
