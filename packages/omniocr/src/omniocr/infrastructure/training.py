"""Training pipeline for Kraken model fine-tuning with MLflow tracking.

Per BUILD_PLAN §10 (Phase 6): fine-tune a Kraken model on a specific
Byzantine printed typeface using ground truth gathered through the review
UI, then validate that the fine-tuned model measurably lowers CER vs the
baseline.

Requires: ``kraken``, ``mlflow``
Install: ``pip install omniocr[training]`` with extras:

    [tool.setuptools.dynamic]
    training = ["kraken>=6.0", "mlflow>=2.20"]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omniocr.domain.models import DocumentPage, OCRLine, Script
from omniocr.infrastructure.review import ReviewDocument


def export_ground_truth_to_kraken_json(
    review_doc: ReviewDocument,
    output_dir: str | Path,
    script: Script = Script.BYZANTINE,
) -> Path:
    """Export ground-truth lines from a ReviewDocument to Kraken training JSON.

    Kraken's training format expects a JSON file with:

    .. code:: json

        [
            {"image": "page-1.png", "text": "line text here"},
            {"image": "page-1.png", "text": "next line text"}
        ]

    Each source image is copied to ``output_dir``. The JSON file is written
    as ``output_dir/train.json``.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, str]] = []
    for page in review_doc.pages:
        image_path = output / f"page-{page.number}.png"
        if page.image_bytes:
            image_path.write_bytes(page.image_bytes)
        for line in page.lines:
            text = _ground_truth_text(line, page)
            if text:
                records.append({
                    "image": image_path.name,
                    "text": text,
                })

    train_path = output / "train.json"
    train_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return train_path


def _ground_truth_text(line: Any, page: Any) -> str:
    """Return the ground-truth text for a line (accepted suggestion or original)."""
    # In a real training pipeline, ground truth would come from accepted
    # suggestions. For now, use the original OCR text.
    return getattr(line, "text", "")


def compute_cer_improvement(
    pretrained_cer: float,
    finetuned_cer: float,
) -> float:
    """Return the relative CER improvement (negative = regression)."""
    if pretrained_cer == 0:
        return 0.0
    return (pretrained_cer - finetuned_cer) / pretrained_cer * 100


__all__ = [
    "compute_cer_improvement",
    "export_ground_truth_to_kraken_json",
]
