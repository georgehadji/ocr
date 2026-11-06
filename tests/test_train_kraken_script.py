"""The training script's page-image loading: the corrections name pages by number."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

import pytest

from omniocr.domain.models import ModelRef, Script
from omniocr.domain.result import Ok

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
    def __init__(self) -> None:
        self.parent: ModelRef | None = None

    def run_training(self, parent_model: ModelRef, **_: object) -> Ok[None]:
        self.parent = parent_model
        return Ok(None)


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolve: Callable[..., str],
    *extra: str,
) -> ModelRef | None:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "1.png").write_bytes(b"page")
    module = _load_script()
    orchestrator = _CapturingOrchestrator()
    monkeypatch.setattr(module, "create_training_pipeline", lambda **_: orchestrator)
    monkeypatch.setattr(module, "resolve_kraken_model", resolve)
    argv = ["train_kraken.py", "--corpus", str(tmp_path), "--pages-dir", str(pages), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    module.main()
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
