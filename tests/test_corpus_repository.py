"""Tests for FileCorpusRepository — filesystem-backed corpus discovery."""

from __future__ import annotations

from pathlib import Path

from omniocr.domain.corpus import SplitName
from omniocr.domain.models import Script
from omniocr.infrastructure.corpus_repository import FileCorpusRepository


def test_pages_returns_empty_for_missing_split_dir(tmp_path: Path) -> None:
    repo = FileCorpusRepository(tmp_path)

    assert repo.pages(SplitName.TRAIN) == []


def test_pages_discovers_matching_image_and_ground_truth_pairs(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "ancient-1.png").write_bytes(b"fake png")
    (train_dir / "ancient-1.txt").write_text("κείμενον", encoding="utf-8")

    pages = FileCorpusRepository(tmp_path).pages(SplitName.TRAIN)

    assert len(pages) == 1
    page = pages[0]
    assert page.page_id == "ancient-1"
    assert page.image_path == train_dir / "ancient-1.png"
    assert page.ground_truth_path == train_dir / "ancient-1.txt"
    assert page.script == Script.ANCIENT
    assert page.split == SplitName.TRAIN
    assert page.provenance == "corpus:train/ancient-1"
    assert page.is_synthetic is False


def test_pages_skips_images_without_ground_truth(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "orphan.png").write_bytes(b"fake png")

    assert FileCorpusRepository(tmp_path).pages(SplitName.TRAIN) == []


def test_pages_skips_non_image_files(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "readme.md").write_text("not an image")
    (train_dir / "readme.txt").write_text("not ground truth either")

    assert FileCorpusRepository(tmp_path).pages(SplitName.TRAIN) == []


def test_pages_filters_by_script(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    for stem in ("ancient-1", "modern-1"):
        (train_dir / f"{stem}.png").write_bytes(b"fake png")
        (train_dir / f"{stem}.txt").write_text("text", encoding="utf-8")

    pages = FileCorpusRepository(tmp_path).pages(SplitName.TRAIN, script=Script.MODERN)

    assert len(pages) == 1
    assert pages[0].page_id == "modern-1"


def test_pages_recognizes_all_supported_image_extensions(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    for ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
        stem = f"modern-{ext.strip('.')}"
        (train_dir / f"{stem}{ext}").write_bytes(b"fake")
        (train_dir / f"{stem}.txt").write_text("text", encoding="utf-8")

    pages = FileCorpusRepository(tmp_path).pages(SplitName.TRAIN)

    assert len(pages) == 5


def test_infer_script_recognizes_every_known_prefix(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    expected = {
        "ancient-1": Script.ANCIENT,
        "byzantine-1": Script.BYZANTINE,
        "polytonic-1": Script.POLYTONIC,
        "modern-1": Script.MODERN,
        "pontian-1": Script.PONTIAN,
        "unrecognized-1": Script.UNKNOWN,
    }
    for stem in expected:
        (train_dir / f"{stem}.png").write_bytes(b"fake")
        (train_dir / f"{stem}.txt").write_text("text", encoding="utf-8")

    pages = {
        page.page_id: page.script for page in FileCorpusRepository(tmp_path).pages(SplitName.TRAIN)
    }

    assert pages == expected
