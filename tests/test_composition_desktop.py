"""Tests for the desktop composition root's --workers wiring.

A composition root's job is wiring, so these assert the concrete
``max_workers`` value that reaches ``PipelineOrchestrator`` — the engines
themselves are exercised by test_core.py and test_engine_accuracy.py.
"""

from __future__ import annotations

import pytest

from omniocr.composition import desktop
from omniocr.composition.desktop import create_ensemble_pipeline, create_tesseract_pipeline
from omniocr.infrastructure.model_manifest import resolve_kraken_model


def test_tesseract_pipeline_defaults_to_sequential() -> None:
    pipeline = create_tesseract_pipeline()
    assert pipeline._max_workers is None


def test_tesseract_pipeline_workers_reaches_orchestrator() -> None:
    pipeline = create_tesseract_pipeline(workers=4)
    assert pipeline._max_workers == 4


def test_ensemble_pipeline_defaults_to_sequential() -> None:
    pipeline = create_ensemble_pipeline(
        tesseract_language="grc", kraken_model_path="not-a-real-model.mlmodel"
    )
    assert pipeline._max_workers is None


def test_ensemble_pipeline_workers_reaches_orchestrator() -> None:
    pipeline = create_ensemble_pipeline(
        tesseract_language="grc",
        kraken_model_path="not-a-real-model.mlmodel",
        workers=3,
    )
    assert pipeline._max_workers == 3


def test_ensemble_pipeline_resolves_an_empty_model_path_to_the_manifest_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server and cloud settings default the path to "", which Kraken read as the cwd."""
    seen: list[str] = []

    class _Recorder:
        name = "kraken"

        def __init__(self, path: str) -> None:
            seen.append(path)

    monkeypatch.setattr(desktop, "KrakenEngine", _Recorder)

    create_ensemble_pipeline(tesseract_language="grc", kraken_model_path="")

    assert seen == [resolve_kraken_model()]
    assert seen[0].endswith("greek-german_serifs_bsb10234118.mlmodel")
