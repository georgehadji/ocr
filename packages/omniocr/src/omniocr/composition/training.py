"""Training composition root — wires the full training pipeline.

Training is a **batch pipeline**, not the recognition pipeline. It gets its
own root: ``create_training_pipeline(...)`` wiring corrections store, cropper,
exporter, trainer, evaluator, policy, registry.

Recognition composition roots stay untouched, so nothing about training can
slow or destabilise inference.
"""

from __future__ import annotations

from pathlib import Path

from omniocr.application.promotion import BeatsParentOnHeldOut, PromotionPolicy
from omniocr.application.training_orchestrator import TrainingOrchestrator
from omniocr.domain.corpus import SplitName
from omniocr.domain.errors import TrainingError
from omniocr.domain.result import Ok, Result
from omniocr.domain.training import EvaluationReport
from omniocr.infrastructure.alto_training import TrainingDataExporter
from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
from omniocr.infrastructure.corpus_repository import FileCorpusRepository
from omniocr.infrastructure.ketos_trainer import KetosTrainer
from omniocr.infrastructure.kraken import KrakenEngine
from omniocr.infrastructure.line_cropper import PilLineCropper
from omniocr.infrastructure.mlflow_registry import (
    InMemoryModelRegistry,
    MlflowModelRegistry,
)
from omniocr.ports.interfaces import (
    ICorrectionStore,
    ICorpusRepository,
    IEvaluator,
    ILineCropper,
    IModelRegistry,
    IOCREngine,
    ITrainer,
    ITrainingDataExporter,
)


class _CorpusEvaluator(IEvaluator):
    """Evaluate a model against the held-out corpus test split."""

    def __init__(self, corpus: ICorpusRepository) -> None:
        self._corpus = corpus

    def evaluate(
        self, engine: IOCREngine, split: SplitName
    ) -> Result[EvaluationReport, TrainingError]:
        from omniocr.application.evaluation import evaluate as eval_fn

        pages = self._corpus.pages(split)
        if not pages:
            return Ok(
                EvaluationReport(
                    model_hash=engine.name,
                    per_script_cer={},
                    per_script_wer={},
                    sample_count=0,
                    evaluated_on=split.value,
                )
            )
        return eval_fn(engine, pages, split)


def create_training_pipeline(
    corrections_db: str | Path = "corrections.db",
    corpus_root: str | Path | None = None,
    mlflow_tracking_uri: str | None = None,
    mlflow_experiment: str = "omniocr-training",
    min_improvement_pct: float = 1.0,
    min_sample_count: int = 10,
    crop_padding: int = 2,
) -> TrainingOrchestrator:
    """Create a fully-wired training pipeline.

    Args:
        corrections_db: Path to the SQLite corrections store.
        corpus_root: Root path for the evaluation corpus (optional).
        mlflow_tracking_uri: MLflow tracking server URI (optional).
        mlflow_experiment: MLflow experiment name.
        min_improvement_pct: Minimum CER improvement for promotion.
        min_sample_count: Minimum sample count for promotion.
        crop_padding: Padding in pixels for line cropping.

    Returns:
        A ready-to-use ``TrainingOrchestrator``.
    """
    # Persistence
    correction_store: ICorrectionStore = SqliteCorrectionStore(corrections_db)

    # Image processing
    cropper: ILineCropper = PilLineCropper(padding=crop_padding)

    # Data export
    exporter: ITrainingDataExporter = TrainingDataExporter()

    # Training
    trainer: ITrainer = KetosTrainer()

    # Evaluation. Without a corpus there is no evaluator and the orchestrator
    # refuses to train: an unmeasured candidate can never be promoted.
    evaluator: IEvaluator | None = None
    if corpus_root is not None:
        evaluator = _CorpusEvaluator(FileCorpusRepository(corpus_root))

    # Promotion policy
    promotion_policy: PromotionPolicy = BeatsParentOnHeldOut(
        min_improvement_pct=min_improvement_pct,
        min_sample_count=min_sample_count,
    )

    # Model registry
    if mlflow_tracking_uri is not None:
        model_registry: IModelRegistry = MlflowModelRegistry(
            tracking_uri=mlflow_tracking_uri,
            experiment_name=mlflow_experiment,
        )
    else:
        model_registry = InMemoryModelRegistry()

    return TrainingOrchestrator(
        correction_store=correction_store,
        cropper=cropper,
        exporter=exporter,
        trainer=trainer,
        evaluator=evaluator,
        promotion_policy=promotion_policy,
        model_registry=model_registry,
        engine_factory=lambda path: KrakenEngine(path),
    )


__all__ = [
    "TrainingOrchestrator",
    "create_training_pipeline",
]
