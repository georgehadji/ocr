"""The training script's page-image loading: the corrections name pages by number."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "train_kraken.py"


def _load_page_images(pages_dir: Path) -> dict[int, bytes]:
    spec = importlib.util.spec_from_file_location("train_kraken", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    images: dict[int, bytes] = module._load_page_images(pages_dir)
    return images


def test_page_images_are_keyed_by_the_number_in_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "1.png").write_bytes(b"one")
    (tmp_path / "12.JPG").write_bytes(b"twelve")
    (tmp_path / "notes.txt").write_bytes(b"not an image")
    (tmp_path / "cover.png").write_bytes(b"no page number")

    assert _load_page_images(tmp_path) == {1: b"one", 12: b"twelve"}
