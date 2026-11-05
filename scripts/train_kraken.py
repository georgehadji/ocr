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

from omniocr.composition import create_training_pipeline
from omniocr.domain.models import ModelRef
from omniocr.domain.result import Err
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
        required=True,
        type=Path,
        help="Path to the pretrained Kraken .mlmodel file",
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
    return parser.parse_args()


_PAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _load_page_images(pages_dir: Path) -> dict[int, bytes]:
    """Map page number to image bytes, from files named ``<page number>.<ext>``."""
    images: dict[int, bytes] = {}
    for path in sorted(pages_dir.iterdir()):
        if path.suffix.lower() in _PAGE_SUFFIXES and path.stem.isdigit():
            images[int(path.stem)] = path.read_bytes()
    return images


def main() -> None:
    args = _parse_args()

    # Validate inputs
    if not args.base_model.is_file():
        sys.exit(f"Base model not found: {args.base_model}")

    if not args.pages_dir.is_dir():
        sys.exit(f"Pages directory not found: {args.pages_dir}")
    page_images = _load_page_images(args.pages_dir)
    if not page_images:
        sys.exit(f"No page images named <page number>.<ext> in {args.pages_dir}")

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
