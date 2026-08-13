"""Tests for the desktop composition root's --workers wiring.

A composition root's job is wiring, so these assert the concrete
``max_workers`` value that reaches ``PipelineOrchestrator`` — the engines
themselves are exercised by test_core.py and test_engine_accuracy.py.
"""

from __future__ import annotations

from omniocr.composition.desktop import create_ensemble_pipeline, create_tesseract_pipeline


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
