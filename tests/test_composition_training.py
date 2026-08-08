"""Tests for the training composition root — verifying it wires what it says.

A composition root's job is wiring, so these tests assert the concrete types
that come out, and exercise _CorpusEvaluator's two real branches (empty
corpus vs. delegating to the real evaluator).
"""

from __future__ import annotations

from pathlib import Path

from omniocr.application.training_orchestrator import TrainingOrchestrator
from omniocr.composition.training import _CorpusEvaluator, create_training_pipeline
from omniocr.domain.corpus import SplitName
from omniocr.infrastructure.corpus_repository import FileCorpusRepository
from omniocr.infrastructure.ketos_trainer import KetosTrainer
from omniocr.infrastructure.line_cropper import PilLineCropper
from omniocr.infrastructure.mlflow_registry import InMemoryModelRegistry, MlflowModelRegistry


def test_default_pipeline_wires_the_no_corpus_no_mlflow_defaults(tmp_path: Path) -> None:
    orchestrator = create_training_pipeline(corrections_db=tmp_path / "corrections.db")

    assert isinstance(orchestrator, TrainingOrchestrator)
    assert isinstance(orchestrator._cropper, PilLineCropper)
    assert isinstance(orchestrator._trainer, KetosTrainer)
    assert isinstance(orchestrator._model_registry, InMemoryModelRegistry)
    # No corpus_root -> the private _NoOpEvaluator, not _CorpusEvaluator.
    assert not isinstance(orchestrator._evaluator, _CorpusEvaluator)


def test_pipeline_wires_corpus_evaluator_when_corpus_root_given(tmp_path: Path) -> None:
    orchestrator = create_training_pipeline(
        corrections_db=tmp_path / "corrections.db", corpus_root=tmp_path / "corpus"
    )

    assert isinstance(orchestrator._evaluator, _CorpusEvaluator)


def test_pipeline_wires_mlflow_registry_when_tracking_uri_given(tmp_path: Path) -> None:
    orchestrator = create_training_pipeline(
        corrections_db=tmp_path / "corrections.db",
        mlflow_tracking_uri=f"file:{tmp_path / 'mlruns'}",
    )

    assert isinstance(orchestrator._model_registry, MlflowModelRegistry)


def test_corpus_evaluator_returns_zero_sample_report_when_corpus_is_empty(tmp_path: Path) -> None:
    """No pages for the split -> a placeholder report, not an error."""
    corpus = FileCorpusRepository(tmp_path / "empty_corpus")
    evaluator = _CorpusEvaluator(corpus)

    class _FakeEngine:
        name = "fake-engine"

    result = evaluator.evaluate(_FakeEngine(), SplitName.TEST)

    assert result.is_ok()
    report = result.value
    assert report.sample_count == 0
    assert report.model_hash == "fake-engine"
    assert report.evaluated_on == "test"


def test_corpus_evaluator_delegates_to_real_evaluation_when_pages_exist(tmp_path: Path) -> None:
    """With real pages present, evaluation actually runs rather than short-circuiting."""
    from omniocr.domain.models import BBox, Confidence, OCRBlock
    from omniocr.domain.result import Ok

    corpus_root = tmp_path / "corpus"
    test_dir = corpus_root / "test"
    test_dir.mkdir(parents=True)
    (test_dir / "modern-1.png").write_bytes(b"fake png bytes")
    (test_dir / "modern-1.txt").write_text("γειά σου", encoding="utf-8")

    corpus = FileCorpusRepository(corpus_root)
    evaluator = _CorpusEvaluator(corpus)

    class _PerfectEngine:
        name = "perfect-engine"

        def extract(self, page: object, context: object) -> Ok:
            return Ok(
                (
                    OCRBlock(
                        id="b0",
                        text="γειά σου",
                        confidence=Confidence(95.0),
                        bbox=BBox(0, 0, 10, 10),
                    ),
                )
            )

    result = evaluator.evaluate(_PerfectEngine(), SplitName.TEST)

    assert result.is_ok()
    report = result.value
    assert report.sample_count == 1
