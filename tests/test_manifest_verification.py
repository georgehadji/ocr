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


def test_a_git_lfs_pointer_is_refused_with_the_reason(tmp_path: Path) -> None:
    """A checkout without ``git lfs pull`` leaves a stub where the model should be."""
    model = tmp_path / "stub.mlmodel"
    model.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")

    with pytest.raises(ValueError, match="Git LFS pointer.*git lfs pull"):
        _ensemble(model)


def test_is_lfs_pointer_is_false_for_real_bytes_and_for_a_missing_file(tmp_path: Path) -> None:
    from omniocr.infrastructure.model_manifest import is_lfs_pointer

    real = tmp_path / "real.mlmodel"
    real.write_bytes(b"\x08\x02weights")

    assert not is_lfs_pointer(real)
    assert not is_lfs_pointer(tmp_path / "absent.mlmodel")
