"""Fine-tune a Kraken OCR model using the v2 training pipeline.

Usage::

    python scripts/train_kraken.py \\
        --corrections-db ./corrections.db \\
        --corpus tests/corpus --pages-dir ./pages \\
        --output-dir ./training_output \\
        --epochs 10

Requires: ``kraken``, ``Pillow``, (optional) ``mlflow``.
"""

from __future__ import annotations

import argparse
import json
import sys
from enum import IntEnum
from pathlib import Path

from omniocr.composition import create_training_pipeline
from omniocr.domain.models import ModelRef, Script
from omniocr.domain.result import Err
from omniocr.domain.training import TrainingOutcome, TrainingReport
from omniocr.infrastructure.model_manifest import resolve_kraken_model
from omniocr.infrastructure.models import sha256_file


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
        default=None,
        type=Path,
        help="Path to the pretrained Kraken .mlmodel file (default: the newest promoted model "
        "for --script, else the manifest default)",
    )
    parser.add_argument(
        "--script",
        choices=[script.value for script in Script],
        default=Script.POLYTONIC.value,
        help="script variety whose promoted model is the default base (default: polytonic)",
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        help="Evaluation corpus root holding a test/ split of page images + .txt ground truth",
    )
    parser.add_argument(
        "--pages-dir",
        required=True,
        type=Path,
        help="Directory of the page images the corrections refer to, named <page number>.<ext> "
        "(1.png, 2.png, ...)",
    )
    parser.add_argument(
        "--min-samples",
        default=10,
        type=int,
        help="Minimum number of held-out pages the candidate must be judged on",
    )
    parser.add_argument(
        "--mlflow-uri",
        default="sqlite:///mlflow.db",
        help="MLflow tracking URI for the model registry",
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
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object on stdout describing how the run ended "
        "(exit codes: 0 promoted, 1 failed, 2 usage, 3 environment, "
        "4 not promoted, 5 nothing to train on)",
    )
    return parser.parse_args()


_PAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _load_page_images(pages_dir: Path) -> dict[int, bytes]:
    """Map page number to image bytes, from files named ``<page number>.<ext>``."""
    images: dict[int, bytes] = {}
    for path in sorted(pages_dir.iterdir()):
        if path.suffix.lower() in _PAGE_SUFFIXES and path.stem.isdigit():
            images[int(path.stem)] = path.read_bytes()
    return images


class TrainExit(IntEnum):
    """Exit codes, so a caller can branch on how the run ended.

    0-3 mean what they mean for the ``omniocr`` CLI; 4 and 5 are the two ways a
    run can finish cleanly without producing a model.
    """

    PROMOTED = 0
    FAILURE = 1  # training itself failed
    USAGE = 2  # argparse default for bad arguments
    ENVIRONMENT = 3  # a model or directory the run needs is missing
    NOT_PROMOTED = 4  # trained, but the candidate did not beat its parent
    NOTHING_TO_TRAIN = 5  # no accepted corrections, or none made a training line


_BY_OUTCOME = {
    TrainingOutcome.PROMOTED: TrainExit.PROMOTED,
    TrainingOutcome.REFUSED: TrainExit.NOT_PROMOTED,
    TrainingOutcome.NO_CORRECTIONS: TrainExit.NOTHING_TO_TRAIN,
    TrainingOutcome.NO_SAMPLES: TrainExit.NOTHING_TO_TRAIN,
}
_ERRORS = {TrainExit.FAILURE, TrainExit.ENVIRONMENT}


def _emit(
    args: argparse.Namespace, code: TrainExit, outcome: str, message: str, **fields: object
) -> TrainExit:
    """Say how the run ended: one JSON object on stdout with ``--json``, else plain text."""
    if args.json:
        record = {"outcome": outcome, "exit_code": int(code), "message": message, **fields}
        print(json.dumps(record, ensure_ascii=False))
    else:
        print(message, file=sys.stderr if code in _ERRORS else sys.stdout)
    return code


def _describe(report: TrainingReport) -> tuple[str, dict[str, object]]:
    """The message and the extra JSON fields for a finished run."""
    if report.promoted is not None:
        promoted = report.promoted
        return (
            f"Model promoted! Improvement: {promoted.improvement_pct:+.2f}%\n"
            f"Model hash: {promoted.model_ref.model_hash}\n"
            f"Model path: {promoted.model_ref.model_name}",
            {
                "improvement_pct": promoted.improvement_pct,
                "model_hash": promoted.model_ref.model_hash,
                "model_path": promoted.model_ref.model_name,
            },
        )
    if report.refusal is not None:
        reason = report.refusal.reason
        return (
            f"Not promoted: {report.refusal}",
            {"reason": None if reason is None else str(reason)},
        )
    if report.outcome is TrainingOutcome.NO_CORRECTIONS:
        return "Nothing to train on: no accepted corrections.", {}
    return "Nothing to train on: every correction was filtered out of the sample set.", {}


def main() -> int:
    args = _parse_args()

    # Validate inputs
    if args.base_model is None:
        try:
            args.base_model = Path(resolve_kraken_model(script=Script(args.script)))
        except FileNotFoundError as exc:
            return _emit(args, TrainExit.ENVIRONMENT, "error", str(exc))
    if not args.base_model.is_file():
        return _emit(
            args, TrainExit.ENVIRONMENT, "error", f"Base model not found: {args.base_model}"
        )

    if not args.pages_dir.is_dir():
        return _emit(
            args, TrainExit.ENVIRONMENT, "error", f"Pages directory not found: {args.pages_dir}"
        )
    page_images = _load_page_images(args.pages_dir)
    if not page_images:
        return _emit(
            args,
            TrainExit.ENVIRONMENT,
            "error",
            f"No page images named <page number>.<ext> in {args.pages_dir}",
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Wire the training pipeline
    orchestrator = create_training_pipeline(
        corrections_db=args.corrections_db,
        corpus_root=args.corpus,
        mlflow_tracking_uri=args.mlflow_uri,
        min_improvement_pct=args.min_improvement,
        min_sample_count=args.min_samples,
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
    result = orchestrator.run_training(
        parent_model=parent_model,
        page_images=page_images,
        output_dir=args.output_dir,
        hyperparameters=params,
    )

    if isinstance(result, Err):
        return _emit(args, TrainExit.FAILURE, "error", f"Training failed: {result.error}")

    message, fields = _describe(result.value)
    return _emit(
        args, _BY_OUTCOME[result.value.outcome], str(result.value.outcome), message, **fields
    )


if __name__ == "__main__":
    raise SystemExit(main())
