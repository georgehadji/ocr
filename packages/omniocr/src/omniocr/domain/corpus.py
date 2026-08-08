"""Corpus value objects for structured page-level data management.

``provenance`` is required: every page traces to a source with a licence.
``is_synthetic`` lets v1's rendered fixtures serve as pipeline smoke tests
while excluding them from accuracy baselines.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from omniocr.domain.models import Script


class SplitName(str, Enum):
    TRAIN = "train"
    DEV = "dev"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class CorpusPage:
    """A single page in the structured corpus with known split assignment."""

    page_id: str
    image_path: Path
    ground_truth_path: Path
    script: Script
    split: SplitName
    provenance: str
    is_synthetic: bool = False

    def __post_init__(self) -> None:
        if not self.page_id:
            raise ValueError("page_id must not be empty")
        if not self.provenance:
            raise ValueError("provenance must not be empty (source + licence)")
        if not self.image_path.exists():
            raise ValueError(f"image_path does not exist: {self.image_path}")
        if not self.ground_truth_path.exists():
            raise ValueError(f"ground_truth_path does not exist: {self.ground_truth_path}")


@dataclass(frozen=True, slots=True)
class SplitRatios:
    """Deterministic data-split ratios for corpus partitioning."""

    train: float = 0.8
    dev: float = 0.1
    test: float = 0.1

    def __post_init__(self) -> None:
        total = self.train + self.dev + self.test
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"split ratios must sum to 1.0, got {total}")
        for name, val in [("train", self.train), ("dev", self.dev), ("test", self.test)]:
            if val <= 0:
                raise ValueError(f"{name} ratio must be positive, got {val}")


DEFAULT_RATIOS = SplitRatios()


__all__ = [
    "CorpusPage",
    "DEFAULT_RATIOS",
    "SplitName",
    "SplitRatios",
]
