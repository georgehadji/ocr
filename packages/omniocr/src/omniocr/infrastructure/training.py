"""Training utilities — replaced by v2 domain-driven infrastructure.

v1's ``export_ground_truth_to_kraken_json`` and ``compute_cer_improvement``
are superseded by the v2 training pipeline:

- ``export_ground_truth_to_kraken_json`` → ``application/ground_truth.py``
  (``assemble_training_samples``) + ``infrastructure/alto_training.py``
  (``TrainingDataExporter``).
- ``compute_cer_improvement`` → ``application/promotion.py``
  (``BeatsParentOnHeldOut``).

These shims remain for backwards compatibility until all callers are updated.
"""

from __future__ import annotations

from typing import Any


def export_ground_truth_to_kraken_json(
    review_doc: Any,
    output_dir: str,
    script: Any = None,
) -> Any:
    """Deprecated. Use ``assemble_training_samples`` + ``TrainingDataExporter``."""
    raise DeprecationWarning(
        "export_ground_truth_to_kraken_json is deprecated in v2. "
        "Use assemble_training_samples() + TrainingDataExporter instead."
    )


def compute_cer_improvement(
    pretrained_cer: float,
    finetuned_cer: float,
) -> float:
    """Return the relative CER improvement (negative = regression).

    Kept for external scripts that import it; prefers the promotion policy
    for new code.
    """
    if pretrained_cer == 0:
        return 0.0
    return (pretrained_cer - finetuned_cer) / pretrained_cer * 100


__all__ = [
    "compute_cer_improvement",
    "export_ground_truth_to_kraken_json",
]
