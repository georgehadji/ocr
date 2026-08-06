"""Fine-tune a Kraken OCR model using the v2 training pipeline.

Usage::

    python scripts/train_kraken.py \\
        --corrections-db ./corrections.db \\
        --base-model /path/to/kraken_base.mlmodel \\
        --output-dir ./training_output \\
        --epochs 10

Requires: ``kraken``, ``Pillow``, (optional) ``mlflow``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from omniocr.application.promotion import BeatsParentOnHeldOut
from omniocr.application.training_orchestrator import TrainingOrchestrator
from omniocr.domain.corpus import SplitName
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import ModelRef
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import EvaluationReport
from omniocr.infrastructure.alto_training import TrainingDataExporter
from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
from omniocr.infrastructure.ketos_trainer import KetosTrainer
from omniocr.infrastructure.line_cropper import PilLineCropper
from omniocr.infrastructure.mlflow_registry import MlflowModelRegistry
from omniocr.infrastructure.models import sha256_file
from omniocr.ports.interfaces import IEvaluator


class _CliEvaluator(IEvaluator):
    """Minimal evaluator for CLI use — records model hash without corpus eval."""

    def __init__(self, base_model: Path) -> None:
        self._model_hash = sha256_file(base_model)

    def evaluate(self, engine: object, split: SplitName) -> Result[EvaluationReport, TrainingError]:
        return Ok(
            EvaluationReport(
                model_hash=self._model_hash,
                per_script_cer={},
                per_script_wer={},
                sample_count=0,
                evaluated_on=split.value,
            )
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune a Kraken model on human-verified corrections (v2)"
    )
    parser.add_argument(
        "--corrections-db",
        default="corrections.db",
        type=Path,
        help="Path to the SQLite corrections store",
    )
    parser.add_argument(
        "--base-model",
        required=True,
        type=Path,
        help="Path to the pretrained Kraken .mlmodel file",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("training_output"),
        type=Path,
        help="Output directory for training artifacts",
    )
    parser.add_argument(
        "--epochs",
        default=10,
        type=int,
        help="Number of training epochs",
    )
    parser.add_argument(
        "--learning-rate",
        default=None,
        type=float,
        help="Learning rate for training",
    )
    parser.add_argument(
        "--batch-size",
        default=None,
        type=int,
        help="Batch size for training",
    )
    parser.add_argument(
        "--min-improvement",
        default=1.0,
        type=float,
        help="Minimum CER improvement percentage for promotion",
    )
    parser.add_argument(
        "--db-corrected-by",
        default="cli-user",
        help="Default corrected_by for any corrections added",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Validate inputs
    if not args.base_model.is_file():
        sys.exit(f"Base model not found: {args.base_model}")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Wire the training pipeline
    correction_store = SqliteCorrectionStore(args.corrections_db)
    cropper = PilLineCropper(padding=2)
    exporter = TrainingDataExporter()
    trainer = KetosTrainer()
    evaluator = _CliEvaluator(args.base_model)

    promotion_policy = BeatsParentOnHeldOut(
        min_improvement_pct=args.min_improvement,
    )

    model_registry = MlflowModelRegistry()

    orchestrator = TrainingOrchestrator(
        correction_store=correction_store,
        cropper=cropper,
        exporter=exporter,
        trainer=trainer,
        evaluator=evaluator,
        promotion_policy=promotion_policy,
        model_registry=model_registry,
    )

    # Build parent model ref
    parent_model = ModelRef(
        engine="kraken",
        model_name=str(args.base_model),
        model_hash=sha256_file(args.base_model),
    )

    # Hyperparameters
    params: dict[str, str] = {"epochs": str(args.epochs)}
    if args.learning_rate is not None:
        params["learning_rate"] = str(args.learning_rate)
    if args.batch_size is not None:
        params["batch_size"] = str(args.batch_size)

    # Run the training pipeline
    # Note: page_images dict must be populated from the corpus in production
    result = orchestrator.run_training(
        parent_model=parent_model,
        page_images={},
        output_dir=args.output_dir,
        hyperparameters=params,
    )

    if isinstance(result, Err):
        sys.exit(f"Training failed: {result.error}")

    promoted = result.value
    if promoted is None:
        print("Training completed but candidate did not meet promotion criteria.")
        print("Check logs for details.")
    else:
        print(f"Model promoted! Improvement: {promoted.improvement_pct:+.2f}%")
        print(f"Model hash: {promoted.model_ref.model_hash}")
        print(f"Model path: {promoted.model_ref.model_name}")


if __name__ == "__main__":
    main()
