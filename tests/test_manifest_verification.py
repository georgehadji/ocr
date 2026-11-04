"""The ensemble root refuses a Kraken model that differs from its pinned hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omniocr.composition import desktop
from omniocr.infrastructure.model_manifest import ManifestEntry, ModelManifest


def _pin(tmp_path: Path, model: Path, sha256: str) -> None:
    manifest = ModelManifest(tmp_path / "manifest.json")
    manifest.add(ManifestEntry("kraken", model.name, "test", "MIT", sha256))


def _ensemble(model: Path) -> object:
    return desktop.create_ensemble_pipeline(tesseract_language="grc", kraken_model_path=model)


@pytest.fixture(autouse=True)
def _manifest_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "MANIFEST_PATH", tmp_path / "manifest.json")


def test_a_model_matching_its_pinned_hash_builds(tmp_path: Path) -> None:
    model = tmp_path / "good.mlmodel"
    model.write_bytes(b"weights")
    _pin(tmp_path, model, hashlib.sha256(b"weights").hexdigest())

    assert _ensemble(model) is not None


def test_a_model_that_differs_from_its_pinned_hash_is_refused(tmp_path: Path) -> None:
    model = tmp_path / "swapped.mlmodel"
    model.write_bytes(b"other weights")
    _pin(tmp_path, model, hashlib.sha256(b"weights").hexdigest())

    with pytest.raises(ValueError, match="does not match the SHA-256 pinned"):
        _ensemble(model)


def test_a_model_the_manifest_does_not_list_is_allowed(tmp_path: Path) -> None:
    model = tmp_path / "finetuned.mlmodel"
    model.write_bytes(b"custom")

    assert _ensemble(model) is not None
