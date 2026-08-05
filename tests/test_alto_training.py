"""Tests for TrainingDataExporter — line-crop images + JSON manifest for ketos."""

from __future__ import annotations

import json
from pathlib import Path

from omniocr.domain.models import Script
from omniocr.domain.training import TrainingSample
from omniocr.infrastructure.alto_training import TrainingDataExporter


def _sample(source_dir: Path, name: str = "line-1.png", text: str = "κεφάλαιον") -> TrainingSample:
    source_dir.mkdir(parents=True, exist_ok=True)
    image_path = source_dir / name
    image_path.write_bytes(b"fake line crop bytes")
    return TrainingSample(image_path=image_path, text=text, script=Script.POLYTONIC)


def test_export_writes_manifest_and_copies_images(tmp_path: Path) -> None:
    sample = _sample(tmp_path / "source")
    output = tmp_path / "out"

    result = TrainingDataExporter().export([sample], output)

    assert result.is_ok()
    manifest_path = result.value
    assert manifest_path == output / "train.json"
    assert manifest_path.is_file()
    assert (output / "images" / "line-1.png").read_bytes() == b"fake line crop bytes"


def test_export_manifest_records_relative_image_paths_and_text(tmp_path: Path) -> None:
    sample = _sample(tmp_path / "source")
    output = tmp_path / "out"

    result = TrainingDataExporter().export([sample], output)

    records = json.loads(result.value.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert Path(records[0]["image"]).as_posix() == "images/line-1.png"
    assert records[0]["text"] == "κεφάλαιον"


def test_export_handles_multiple_samples(tmp_path: Path) -> None:
    source = tmp_path / "source"
    samples = [
        _sample(source, name="line-1.png", text="πρώτη γραμμή"),
        _sample(source, name="line-2.png", text="δεύτερη γραμμή"),
    ]
    output = tmp_path / "out"

    result = TrainingDataExporter().export(samples, output)

    records = json.loads(result.value.read_text(encoding="utf-8"))
    assert len(records) == 2
    assert {r["text"] for r in records} == {"πρώτη γραμμή", "δεύτερη γραμμή"}


def test_export_skips_copying_when_source_image_missing(tmp_path: Path) -> None:
    """A sample whose image no longer exists still gets a manifest entry.

    The manifest entry is emitted regardless — the destination path is
    recorded even though no bytes were copied. This documents that behavior
    rather than silently accepting whatever happens.
    """
    missing_path = tmp_path / "source" / "gone.png"
    sample = TrainingSample(image_path=missing_path, text="text", script=Script.MODERN)
    output = tmp_path / "out"

    result = TrainingDataExporter().export([sample], output)

    assert result.is_ok()
    assert not (output / "images" / "gone.png").exists()
    records = json.loads(result.value.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert Path(records[0]["image"]).as_posix() == "images/gone.png"
    assert records[0]["text"] == "text"


def test_export_creates_output_directory_when_absent(tmp_path: Path) -> None:
    sample = _sample(tmp_path / "source")
    output = tmp_path / "does" / "not" / "exist" / "yet"

    result = TrainingDataExporter().export([sample], output)

    assert result.is_ok()
    assert output.is_dir()
