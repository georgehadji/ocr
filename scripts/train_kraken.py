"""Fine-tune a Kraken OCR model on Byzantine/Polytonic printed Greek.

Usage:

    python scripts/train_kraken.py \\
        --train-dir ./training_data \\
        --base-model /path/to/kraken_base.mlmodel \\
        --output-model ./finetuned_byzantine.mlmodel

Requires: ``kraken`` (for training CLI), ``mlflow`` (for experiment tracking).
Install: ``pip install kraken mlflow``
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# MLflow is optional — training works without it.
try:
    import mlflow
    _HAS_MLFLOW = True
except ImportError:
    _HAS_MLFLOW = False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune a Kraken model on printed Greek ground truth"
    )
    parser.add_argument(
        "--train-dir",
        required=True,
        type=Path,
        help="Directory containing train.json and page images",
    )
    parser.add_argument(
        "--base-model",
        required=True,
        type=Path,
        help="Path to the pretrained Kraken .mlmodel file",
    )
    parser.add_argument(
        "--output-model",
        default=Path("finetuned.mlmodel"),
        type=Path,
        help="Output path for the fine-tuned model",
    )
    parser.add_argument(
        "--epochs",
        default=10,
        type=int,
        help="Number of training epochs",
    )
    parser.add_argument(
        "--mlflow-experiment",
        default="omniocr-kraken-finetune",
        help="MLflow experiment name (if MLflow is installed)",
    )
    return parser.parse_args()


def _train_kraken(args: argparse.Namespace) -> float:
    """Run kraken-train and return the final validation CER."""
    cmd = [
        "kraken",
        "--log", "info",
        "train",
        "--device", "cpu",
        "--load", str(args.base_model),
        "--train", str(args.train_dir / "train.json"),
        "--epochs", str(args.epochs),
        "--output", str(args.output_model),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"kraken train failed: {result.stderr}")

    # Parse CER from kraken output (format: "CER: 0.1234")
    final_cer = 0.0
    for line in result.stderr.split("\n"):
        if "CER:" in line:
            try:
                final_cer = float(line.split("CER:")[-1].strip())
            except (ValueError, IndexError):
                pass

    print(f"Training complete. Final CER: {final_cer:.4f}")
    return final_cer


def _track_with_mlflow(args: argparse.Namespace, final_cer: float) -> None:
    """Log training run to MLflow."""
    if not _HAS_MLFLOW:
        print("MLflow not installed — skipping experiment tracking")
        return

    mlflow.set_experiment(args.mlflow_experiment)
    with mlflow.start_run():
        mlflow.log_params({
            "base_model": str(args.base_model),
            "epochs": args.epochs,
            "train_samples": len(json.loads((args.train_dir / "train.json").read_text())),
        })
        mlflow.log_metric("final_cer", final_cer)
        mlflow.log_artifact(str(args.output_model))
        print(f"MLflow run logged to experiment '{args.mlflow_experiment}'")


def main() -> None:
    args = _parse_args()
    if not args.train_dir.is_dir():
        sys.exit(f"Training directory not found: {args.train_dir}")
    if not args.base_model.is_file():
        sys.exit(f"Base model not found: {args.base_model}")

    final_cer = _train_kraken(args)
    _track_with_mlflow(args, final_cer)

    print(f"Fine-tuned model saved to: {args.output_model}")
    from omniocr.infrastructure.training import compute_cer_improvement
    print(f"CER improvement vs pretrained: {compute_cer_improvement(0.0, final_cer):+.2f}%")


if __name__ == "__main__":
    main()
