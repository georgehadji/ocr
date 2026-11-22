"""The training script's page-image loading: the corrections name pages by number."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest

from omniocr.domain.models import ModelRef, Script
from omniocr.domain.errors import PromotionRefused, RefusalReason, TrainingError
from omniocr.domain.result import Err, Ok
from omniocr.domain.training import TrainingOutcome, TrainingReport

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "train_kraken.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("train_kraken", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_page_images(pages_dir: Path) -> dict[int, bytes]:
    images: dict[int, bytes] = _load_script()._load_page_images(pages_dir)
    return images


class _CapturingOrchestrator:
    def __init__(self, result: object = None) -> None:
        self.parent: ModelRef | None = None
        self._result = result or Ok(TrainingReport(TrainingOutcome.NO_CORRECTIONS))

    def run_training(self, parent_model: ModelRef, **_: object) -> object:
        self.parent = parent_model
        return self._result


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolve: Callable[..., str],
    *extra: str,
    result: object = None,
    code: list[int] | None = None,
) -> ModelRef | None:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    (pages / "1.png").write_bytes(b"page")
    module = _load_script()
    orchestrator = _CapturingOrchestrator(result)
    monkeypatch.setattr(module, "create_training_pipeline", lambda **_: orchestrator)
    monkeypatch.setattr(module, "resolve_kraken_model", resolve)
    argv = ["train_kraken.py", "--corpus", str(tmp_path), "--pages-dir", str(pages), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    exit_code = module.main()
    if code is not None:
        code.append(int(exit_code))
    return orchestrator.parent


def test_base_model_defaults_to_the_resolved_model_for_the_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolved = tmp_path / "promoted.mlmodel"
    resolved.write_bytes(b"weights")
    seen: list[Script | None] = []

    def resolve(script: Script | None = None) -> str:
        seen.append(script)
        return str(resolved)

    parent = _run_main(monkeypatch, tmp_path, resolve, "--script", "ancient")

    assert parent is not None and parent.model_name == str(resolved)
    assert seen == [Script.ANCIENT]


def test_explicit_base_model_skips_the_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit.mlmodel"
    explicit.write_bytes(b"weights")

    def resolve(script: Script | None = None) -> str:
        raise AssertionError("resolver must not run when --base-model is given")

    parent = _run_main(monkeypatch, tmp_path, resolve, "--base-model", str(explicit))

    assert parent is not None and parent.model_name == str(explicit)


def test_page_images_are_keyed_by_the_number_in_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "1.png").write_bytes(b"one")
    (tmp_path / "12.JPG").write_bytes(b"twelve")
    (tmp_path / "notes.txt").write_bytes(b"not an image")
    (tmp_path / "cover.png").write_bytes(b"no page number")

    assert _load_page_images(tmp_path) == {1: b"one", 12: b"twelve"}


# --- how the run ended: exit code and --json --------------------------------


def _ended(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    result: object,
    *extra: str,
) -> tuple[int, str, str]:
    base = tmp_path / "base.mlmodel"
    base.write_bytes(b"weights")
    code: list[int] = []
    _run_main(
        monkeypatch,
        tmp_path,
        lambda script=None: str(base),
        "--base-model",
        str(base),
        *extra,
        result=result,
        code=code,
    )
    captured = capsys.readouterr()
    return code[0], captured.out, captured.err


def _promoted_report(tmp_path: Path) -> TrainingReport:
    from test_model_registry import _promoted

    return TrainingReport(TrainingOutcome.PROMOTED, promoted=_promoted(tmp_path, b"w"))


def test_a_promoted_run_exits_zero(monkeypatch, tmp_path, capsys) -> None:
    code, out, _ = _ended(monkeypatch, tmp_path, capsys, Ok(_promoted_report(tmp_path)))

    assert code == 0
    assert "Model promoted!" in out


def test_a_refused_run_exits_four_and_says_why(monkeypatch, tmp_path, capsys) -> None:
    refused = TrainingReport(
        TrainingOutcome.REFUSED,
        refusal=PromotionRefused("script polytonic CER regressed", RefusalReason.SCRIPT_REGRESSED),
    )

    code, out, _ = _ended(monkeypatch, tmp_path, capsys, Ok(refused))

    assert code == 4
    assert "Not promoted: script polytonic CER regressed" in out


@pytest.mark.parametrize("outcome", [TrainingOutcome.NO_CORRECTIONS, TrainingOutcome.NO_SAMPLES])
def test_nothing_to_train_on_exits_five(monkeypatch, tmp_path, capsys, outcome) -> None:
    code, out, _ = _ended(monkeypatch, tmp_path, capsys, Ok(TrainingReport(outcome)))

    assert code == 5
    assert "Nothing to train on" in out


def test_a_training_error_exits_one_on_stderr(monkeypatch, tmp_path, capsys) -> None:
    code, out, err = _ended(monkeypatch, tmp_path, capsys, Err(TrainingError("ketos exploded")))

    assert code == 1
    assert "Training failed: ketos exploded" in err
    assert out == ""


def test_a_missing_base_model_exits_three(monkeypatch, tmp_path, capsys) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "1.png").write_bytes(b"page")
    module = _load_script()
    argv = [
        "train_kraken.py",
        "--corpus",
        str(tmp_path),
        "--pages-dir",
        str(pages),
        "--base-model",
        str(tmp_path / "absent.mlmodel"),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert module.main() == 3
    assert "Base model not found" in capsys.readouterr().err


def test_json_is_one_object_a_caller_can_branch_on(monkeypatch, tmp_path, capsys) -> None:
    import json

    refused = TrainingReport(
        TrainingOutcome.REFUSED,
        refusal=PromotionRefused(
            "improvement 0.10% is below minimum", RefusalReason.BELOW_THRESHOLD
        ),
    )

    code, out, _ = _ended(monkeypatch, tmp_path, capsys, Ok(refused), "--json")

    record = json.loads(out)
    assert code == 4
    assert record["outcome"] == "refused"
    assert record["exit_code"] == 4
    assert record["reason"] == "below_threshold"


def test_json_reports_a_usage_error_as_json(monkeypatch, capsys) -> None:
    """A --json caller parses stdout; argparse's plain-text error would break it."""
    import json

    module = _load_script()
    monkeypatch.setattr(sys, "argv", ["train_kraken.py", "--json", "--corpus", "c"])

    with pytest.raises(SystemExit) as exited:
        module.main()

    record = json.loads(capsys.readouterr().out)
    assert exited.value.code == 2
    assert record["outcome"] == "error"
    assert record["exit_code"] == 2
    assert "--pages-dir" in record["message"]


def test_without_json_a_usage_error_stays_argparse_text(monkeypatch, capsys) -> None:
    module = _load_script()
    monkeypatch.setattr(sys, "argv", ["train_kraken.py", "--corpus", "c"])

    with pytest.raises(SystemExit) as exited:
        module.main()

    captured = capsys.readouterr()
    assert exited.value.code == 2
    assert captured.out == ""
    assert "--pages-dir" in captured.err


class _Crashes:
    def run_training(self, **_: object) -> object:
        raise RuntimeError("disk vanished")


def _crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> int:
    base = tmp_path / "base.mlmodel"
    base.write_bytes(b"weights")
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "1.png").write_bytes(b"page")
    module = _load_script()
    monkeypatch.setattr(module, "create_training_pipeline", lambda **_: _Crashes())
    argv = ["train_kraken.py", "--corpus", str(tmp_path), "--pages-dir", str(pages)]
    monkeypatch.setattr(sys, "argv", [*argv, "--base-model", str(base), *extra])
    return int(module.main())


def test_json_reports_a_crash_as_json(monkeypatch, tmp_path, capsys) -> None:
    import json

    code = _crash(monkeypatch, tmp_path, "--json")

    record = json.loads(capsys.readouterr().out)
    assert code == 1
    assert record == {
        "outcome": "error",
        "exit_code": 1,
        "message": "Training crashed: RuntimeError: disk vanished",
    }


def test_without_json_a_crash_keeps_its_traceback(monkeypatch, tmp_path) -> None:
    with pytest.raises(RuntimeError, match="disk vanished"):
        _crash(monkeypatch, tmp_path)


def test_json_of_a_promotion_carries_the_model(monkeypatch, tmp_path, capsys) -> None:
    import json

    report = _promoted_report(tmp_path)

    _, out, _ = _ended(monkeypatch, tmp_path, capsys, Ok(report), "--json")

    record = json.loads(out)
    assert record["outcome"] == "promoted"
    assert record["model_hash"] == report.promoted.model_ref.model_hash
    assert record["improvement_pct"] == report.promoted.improvement_pct
