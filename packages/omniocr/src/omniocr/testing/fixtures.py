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


def load_provenance() -> dict[str, dict[str, str]]:
    """Return the per-fixture provenance record from corpus/PROVENANCE.json."""
    path = _get_corpus() / "PROVENANCE.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("fixtures", {}))


def fixture_tier(fixture_id: str) -> str:
    """Return ``'synthetic'``, ``'scan'``, or ``'unknown'`` for a fixture.

    Accuracy gates must consult this. A synthetic fixture is a machine render
    of a known string: it proves an engine reads Greek, but its CER says
    nothing about performance on a real page, and treating the two alike is
    how a 0.0076 CER on rendered Arial gets mistaken for book accuracy.
    """
    return load_provenance().get(fixture_id, {}).get("tier", "unknown")


def list_scan_ids() -> list[str]:
    """Return only fixtures backed by a real page image and human transcription."""
    return [fid for fid in list_fixture_ids() if fixture_tier(fid) == "scan"]
