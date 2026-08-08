"""Promotion policy — the guard that makes learning real.

A pure functional module with no I/O. Every judgment is a pure function
that returns a ``Result``, so the promotion decision is transparent,
testable, and auditable.
"""

from __future__ import annotations

from typing import Protocol

from omniocr.domain.errors import PromotionRefused
from omniocr.domain.models import ModelRef
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import EvaluationReport, ModelCandidate, PromotedModel


class PromotionPolicy(Protocol):
    """Decide whether a candidate model should be promoted over its parent."""

    def decide(
        self,
        candidate: ModelCandidate,
        candidate_report: EvaluationReport,
        parent_report: EvaluationReport,
    ) -> Result[PromotedModel, PromotionRefused]: ...


class BeatsParentOnHeldOut:
    """Promote only on measured improvement over the parent on the test split.

    Rules:
    1. Refuse unless ``evaluated_on == SplitName.TEST``.
    2. Refuse if mean CER did not improve by at least ``min_improvement_pct``.
    3. Refuse if **any** script's CER regressed.
    4. Refuse if ``sample_count`` is below the minimum floor.
    5. Every refusal returns a reason and is logged.
    """

    def __init__(
        self,
        min_improvement_pct: float = 1.0,
        min_sample_count: int = 10,
    ) -> None:
        if min_improvement_pct < 0:
            raise ValueError("min_improvement_pct must not be negative")
        if min_sample_count < 1:
            raise ValueError("min_sample_count must be at least 1")
        self._min_improvement_pct = min_improvement_pct
        self._min_sample_count = min_sample_count

    def decide(
        self,
        candidate: ModelCandidate,
        candidate_report: EvaluationReport,
        parent_report: EvaluationReport,
    ) -> Result[PromotedModel, PromotionRefused]:
        # Rule 1: must be evaluated on TEST split
        if candidate_report.evaluated_on != "test":
            return Err(
                PromotionRefused(f"evaluated on '{candidate_report.evaluated_on}', expected 'test'")
            )

        # Rule 4: minimum sample count
        if candidate_report.sample_count < self._min_sample_count:
            return Err(
                PromotionRefused(
                    f"sample count {candidate_report.sample_count} is below "
                    f"minimum {self._min_sample_count}"
                )
            )

        # Compute mean CER for both reports
        candidate_mean_cer = _mean_cer(candidate_report)
        parent_mean_cer = _mean_cer(parent_report)

        # Rule 3: refuse if any script regressed
        for script in candidate_report.per_script_cer:
            candidate_cer = candidate_report.per_script_cer.get(script, 0.0)
            parent_cer = parent_report.per_script_cer.get(script, 0.0)
            if candidate_cer > parent_cer:
                return Err(
                    PromotionRefused(
                        f"script {script.value} CER regressed: "
                        f"{parent_cer:.4f} → {candidate_cer:.4f}"
                    )
                )

        # Rule 2: refuse if improvement is below threshold
        if parent_mean_cer == 0.0:
            return Err(
                PromotionRefused("parent mean CER is 0 — cannot compute relative improvement")
            )

        improvement_pct = (parent_mean_cer - candidate_mean_cer) / parent_mean_cer * 100.0
        if improvement_pct < self._min_improvement_pct:
            return Err(
                PromotionRefused(
                    f"improvement {improvement_pct:.2f}% is below "
                    f"minimum {self._min_improvement_pct}% "
                    f"(parent CER: {parent_mean_cer:.4f}, "
                    f"candidate CER: {candidate_mean_cer:.4f})"
                )
            )

        # All rules passed — promote
        promoted = PromotedModel(
            model_ref=ModelRef(
                engine="kraken",
                model_name=str(candidate.path),
                model_hash=candidate.model_hash,
            ),
            report=candidate_report,
            improvement_pct=improvement_pct,
            checkpoint=candidate.path,
        )
        return Ok(promoted)


def _mean_cer(report: EvaluationReport) -> float:
    """Compute mean CER across all scripts in a report."""
    if not report.per_script_cer:
        return 0.0
    return sum(report.per_script_cer.values()) / len(report.per_script_cer)


def refuse_unless_test_split(
    candidate_report: EvaluationReport,
) -> Result[None, PromotionRefused]:
    """Refuse promotion unless evaluated on the TEST split."""
    if candidate_report.evaluated_on != "test":
        return Err(
            PromotionRefused(f"evaluated on '{candidate_report.evaluated_on}', expected 'test'")
        )
    return Ok(None)


__all__ = [
    "BeatsParentOnHeldOut",
    "PromotionPolicy",
    "refuse_unless_test_split",
]
