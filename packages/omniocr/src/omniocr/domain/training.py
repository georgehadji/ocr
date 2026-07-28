"""Training value objects with type-state transitions.

``ModelCandidate`` → ``PromotedModel`` is a type-state transition obtainable
only through ``PromotionPolicy``. A candidate cannot be wired into the router;
the router accepts ``PromotedModel``. This makes "never ship an unproven model"
structural.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from omniocr.domain.models import ModelRef, Script


@dataclass(frozen=True, slots=True)
class TrainingSample:
    """One line image + its transcription. The image is a LINE crop, never a full page."""

    image_path: Path
    text: str
    script: Script

    def __post_init__(self) -> None:
        if not self.image_path.name:
            raise ValueError("image_path must name a file")
        if not self.text:
            raise ValueError("text must not be empty")


@dataclass(frozen=True, slots=True)
class TrainingRun:
    """Metadata for one training execution."""

    run_id: str
    parent_model: ModelRef
    sample_count: int
    hyperparameters: Mapping[str, str]
    started_at: str

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if self.sample_count < 0:
            raise ValueError("sample_count must not be negative")


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """A fine-tuned model that has NOT yet earned promotion."""

    path: Path
    model_hash: str
    parent: ModelRef
    run_id: str

    def __post_init__(self) -> None:
        if not self.path.exists():
            raise ValueError(f"model path does not exist: {self.path}")
        if not self.model_hash:
            raise ValueError("model_hash must not be empty")


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Measured accuracy of a model against a specific data split.

    ``evaluated_on`` must be the split name — this prevents accidentally
    evaluating on training data and reporting fictional gains.
    """

    model_hash: str
    per_script_cer: Mapping[Script, float]
    per_script_wer: Mapping[Script, float]
    sample_count: int
    evaluated_on: str

    def __post_init__(self) -> None:
        if not self.model_hash:
            raise ValueError("model_hash must not be empty")
        if self.sample_count < 0:
            raise ValueError("sample_count must not be negative")
        if not self.evaluated_on:
            raise ValueError("evaluated_on must not be empty")
        for cer in self.per_script_cer.values():
            if cer < 0:
                raise ValueError(f"CER must not be negative: {cer}")
        for wer in self.per_script_wer.values():
            if wer < 0:
                raise ValueError(f"WER must not be negative: {wer}")


@dataclass(frozen=True, slots=True)
class PromotedModel:
    """A candidate that beat its parent on held-out data. Only this type is routable."""

    model_ref: ModelRef
    report: EvaluationReport
    improvement_pct: float


__all__ = [
    "EvaluationReport",
    "ModelCandidate",
    "PromotedModel",
    "TrainingRun",
    "TrainingSample",
]
