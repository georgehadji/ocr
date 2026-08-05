"""Tests for ModelManifest — hash-pinned artifact registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from omniocr.infrastructure.model_manifest import ManifestEntry, ModelManifest


def _entry(name: str = "greek-porson", sha256: str = "abc123") -> ManifestEntry:
    return ManifestEntry(
        engine="kraken",
        name=name,
        source="https://example.org/models",
        licence="CC-BY-4.0",
        sha256=sha256,
        description="19c Porson Greek",
        params=("cpu",),
    )


def test_missing_manifest_file_loads_as_empty(tmp_path: Path) -> None:
    manifest = ModelManifest(tmp_path / "no_such_manifest.json")

    assert manifest.all() == ()
    assert manifest.get("anything") is None


def test_malformed_json_degrades_to_empty_rather_than_raising(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")

    manifest = ModelManifest(manifest_path)

    assert manifest.all() == ()


def test_entry_missing_required_key_degrades_to_empty(tmp_path: Path) -> None:
    """A manifest entry missing e.g. sha256 must not crash the whole load."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"models": [{"engine": "kraken", "name": "incomplete"}]}),
        encoding="utf-8",
    )

    manifest = ModelManifest(manifest_path)

    assert manifest.all() == ()


def test_add_persists_and_reloads_from_disk(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = ModelManifest(manifest_path)

    manifest.add(_entry())

    assert manifest_path.is_file()
    reloaded = ModelManifest(manifest_path)
    entry = reloaded.get("greek-porson")
    assert entry is not None
    assert entry.engine == "kraken"
    assert entry.sha256 == "abc123"
    assert entry.params == ("cpu",)


def test_add_overwrites_an_existing_entry_by_name(tmp_path: Path) -> None:
    manifest = ModelManifest(tmp_path / "manifest.json")
    manifest.add(_entry(sha256="old-hash"))

    manifest.add(_entry(sha256="new-hash"))

    assert manifest.get("greek-porson").sha256 == "new-hash"
    assert len(manifest.all()) == 1


def test_verify_returns_false_for_unknown_name(tmp_path: Path) -> None:
    manifest = ModelManifest(tmp_path / "manifest.json")

    assert manifest.verify("unknown", tmp_path / "some.mlmodel") is False


def test_verify_returns_false_when_artifact_missing(tmp_path: Path) -> None:
    manifest = ModelManifest(tmp_path / "manifest.json")
    manifest.add(_entry())

    assert manifest.verify("greek-porson", tmp_path / "does_not_exist.mlmodel") is False


def test_verify_returns_true_when_hash_matches(tmp_path: Path) -> None:
    model_path = tmp_path / "model.mlmodel"
    model_bytes = b"fake model weights"
    model_path.write_bytes(model_bytes)
    real_hash = hashlib.sha256(model_bytes).hexdigest()

    manifest = ModelManifest(tmp_path / "manifest.json")
    manifest.add(_entry(sha256=real_hash))

    assert manifest.verify("greek-porson", model_path) is True


def test_verify_returns_false_when_hash_mismatches(tmp_path: Path) -> None:
    model_path = tmp_path / "model.mlmodel"
    model_path.write_bytes(b"tampered bytes")

    manifest = ModelManifest(tmp_path / "manifest.json")
    manifest.add(_entry(sha256="not-the-real-hash"))

    assert manifest.verify("greek-porson", model_path) is False
