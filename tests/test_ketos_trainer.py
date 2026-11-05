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
    images = data_dir / "images"
    images.mkdir()
    (images / "line-1.png").write_bytes(b"png")
    (images / "line-1.gt.txt").write_text("text" + chr(10), encoding="utf-8")
    return data_dir


def _produce(data_dir: Path, payload: bytes) -> None:
    """What ``ketos train`` leaves behind: a ``best_<score>.mlmodel`` in its output dir."""
    out = data_dir.parent / "ketos_out"
    out.mkdir(exist_ok=True)
    (out / "best_0.9000.mlmodel").write_bytes(payload)


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


def test_train_fails_fast_when_there_are_no_line_images(tmp_path: Path) -> None:
    data_dir = tmp_path / "training_data"
    data_dir.mkdir()
    (data_dir / "train.json").write_text("[]", encoding="utf-8")

    result = KetosTrainer().train(data_dir, _parent_ref(_make_parent_model(tmp_path)), {})

    assert result.is_err()
    assert "no line images" in str(result.error)


def test_train_calls_the_real_ketos_interface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The device is a group option, the data is passed as a path list, and the
    defaults suit fine-tuning (a model that ``python -m kraken train`` once
    could not be run at all: ``kraken`` has no ``__main__``)."""
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)
    captured: list[str] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured.extend(cmd)
        _produce(data_dir, b"model")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert KetosTrainer(device="cpu").train(data_dir, _parent_ref(parent_path), {}).is_ok()

    assert captured[1:3] == ["-c", "from kraken.ketos import cli; cli()"]
    assert captured.index("--device") < captured.index("train")
    assert captured[captured.index("--format-type") + 1] == "path"
    assert captured[captured.index("--load") + 1] == str(parent_path)
    assert captured[captured.index("--resize") + 1] == "union"
    assert captured[captured.index("--quit") + 1] == "fixed"
    assert captured[captured.index("--lrate") + 1] == "0.0001"
    listed = Path(captured[captured.index("--training-data") + 1]).read_text(encoding="utf-8")
    assert listed == str((data_dir / "images" / "line-1.png").resolve())


def test_train_accepts_the_manifest_path_the_exporter_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The orchestrator hands the trainer ``export``'s result, i.e. train.json itself."""
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        _produce(data_dir, b"model")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = KetosTrainer().train(data_dir / "train.json", _parent_ref(parent_path), {})

    assert result.is_ok()


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

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        # Simulate ketos producing its output artifact as a side effect.
        _produce(data_dir, b"fine-tuned model bytes")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    trainer = KetosTrainer()

    result = trainer.train(data_dir, _parent_ref(parent_path), {"epochs": "5", "run_id": "run-42"})

    assert result.is_ok()
    candidate = result.value
    assert candidate.path == data_dir.parent / "ketos_finetuned.mlmodel"
    assert candidate.run_id == "run-42"
    assert candidate.parent.model_name == str(parent_path)
    assert len(candidate.model_hash) == 64  # sha256 hex digest


def test_train_includes_optional_hyperparameters_in_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = _make_train_data(tmp_path)
    parent_path = _make_parent_model(tmp_path)
    captured_cmd: list[str] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        captured_cmd.extend(cmd)
        _produce(data_dir, b"model")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    trainer = KetosTrainer(device="cuda:0")

    result = trainer.train(
        data_dir,
        _parent_ref(parent_path),
        {"learning_rate": "0.001", "batch_size": "8", "momentum": "0.99"},
    )

    assert result.is_ok()
    assert captured_cmd[captured_cmd.index("--lrate") + 1] == "0.001"
    assert captured_cmd[captured_cmd.index("--batch-size") + 1] == "8"
    assert captured_cmd[captured_cmd.index("--momentum") + 1] == "0.99"
    assert captured_cmd[captured_cmd.index("--device") + 1] == "cuda:0"


class TestDeviceSelection:
    """Fine-tuning is the most GPU-sensitive step; it must not silently use CPU."""

    @staticmethod
    def _capture_device(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trainer: KetosTrainer
    ) -> str:
        data_dir = _make_train_data(tmp_path)
        parent_path = _make_parent_model(tmp_path)
        captured: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            captured.extend(cmd)
            _produce(data_dir, b"model")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert trainer.train(data_dir, _parent_ref(parent_path), {}).is_ok()
        return captured[captured.index("--device") + 1]

    def test_defaults_to_the_detected_device_not_hardcoded_cpu(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Regression: the default was the literal string "cpu", so a GPU box
        fine-tuned on CPU for hours and nothing said why."""
        monkeypatch.setattr(
            "omniocr.infrastructure.ketos_trainer.select_device", lambda pref: "cuda:0"
        )

        assert self._capture_device(monkeypatch, tmp_path, KetosTrainer()) == "cuda:0"

    def test_falls_back_to_cpu_with_no_gpu(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "omniocr.infrastructure.ketos_trainer.select_device", lambda pref: "cpu"
        )

        assert self._capture_device(monkeypatch, tmp_path, KetosTrainer()) == "cpu"

    def test_construction_does_not_probe_the_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Composition roots build the trainer eagerly; probing imports torch."""
        probed = False

        def _probe(pref: str | None) -> str:
            nonlocal probed
            probed = True
            return "cpu"

        monkeypatch.setattr("omniocr.infrastructure.ketos_trainer.select_device", _probe)

        KetosTrainer()

        assert probed is False
