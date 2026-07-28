"""Fixture loading helpers for synthetic and real OCR corpus pages."""

from __future__ import annotations

import json
from pathlib import Path

import unicodedata


# When imported from tests/conftest.py, override this with the actual
# tests/corpus/ path for robustness against directory restructuring.
CORPUS: Path | None = None


def _get_corpus() -> Path:
    path = CORPUS
    if path is None:
        path = Path(__file__).resolve().parents[5] / "tests" / "corpus"
    return path


def set_corpus_path(path: str | Path) -> None:
    """Override the default corpus path (called from conftest.py)."""
    global CORPUS
    CORPUS = Path(path)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def load_fixture_ground_truth(fixture_id: str) -> str:
    """Return the NFC-normalized ground-truth text for a corpus fixture."""
    path = _get_corpus() / f"{fixture_id}.txt"
    if not path.is_file():
        msg = f"ground-truth file not found: {path}"
        raise FileNotFoundError(msg)
    return _normalize(path.read_text(encoding="utf-8"))


def load_fixture_image_bytes(fixture_id: str) -> bytes:
    """Return the raw PNG bytes for a corpus fixture."""
    path = _get_corpus() / f"{fixture_id}.png"
    if not path.is_file():
        msg = f"fixture image not found: {path}"
        raise FileNotFoundError(msg)
    return path.read_bytes()


def load_baselines() -> dict[str, dict[str, float]]:
    """Load committed CER/WER baselines from corpus/baselines.json."""
    path = _get_corpus() / "baselines.json"
    if not path.is_file():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


def list_fixture_ids() -> list[str]:
    """Return all fixture ids (the stem of each .txt file in the corpus)."""
    return sorted(path.stem for path in _get_corpus().glob("*.txt") if path.stem != "baselines")
