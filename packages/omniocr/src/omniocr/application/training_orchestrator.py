"""Training orchestrator — the imperative shell for the training pipeline.

Pipes-and-Filters pattern sequencing side effects. Every judgment is
delegated to a pure collaborator (promotion, evaluation, ground_truth).
Threads ``Result`` exactly like ``PipelineOrchestrator``, so a failed stage
short-circuits with a typed error rather than raising.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from omniocr.application.ground_truth import (
    SampleSpecification,
    assemble_training_samples,
)
from omniocr.application.promotion import PromotionPolicy
from omniocr.domain.errors import EngineError, TrainingError
from omniocr.domain.models import ModelRef, OCRBlock, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import (
    ModelCandidate,
    PromotedModel,
)
from omniocr.ports.interfaces import (
    ICorrectionStore,
    IEventBus,
    IEvaluator,
    ILineCropper,
    IModelRegistry,
    ITrainer,
    ITrainingDataExporter,
    RawPage,
)
from omniocr.domain.corpus import SplitName

_LOG = logging.getLogger("omniocr.training")


class TrainingOrchestrator:
    """Orchestrate the full training lifecycle: corrections → promotion.

    Pipeline stages:
        corrections → assemble samples → export training data → train
        → evaluate candidate → evaluate parent → promotion policy
        → register (or refuse)
    """

    def __init__(
        self,
        correction_store: ICorrectionStore,
        cropper: ILineCropper,
        exporter: ITrainingDataExporter,
        trainer: ITrainer,
        evaluator: IEvaluator,
        promotion_policy: PromotionPolicy,
        model_registry: IModelRegistry,
        event_bus: IEventBus | None = None,
    ) -> None:
        self._correction_store = correction_store
        self._cropper = cropper
        self._exporter = exporter
        self._trainer = trainer
        self._evaluator = evaluator
        self._promotion_policy = promotion_policy
        self._model_registry = model_registry
        self._event_bus = event_bus

    def run_training(
        self,
        parent_model: ModelRef,
        page_images: dict[int, bytes],
        output_dir: Path,
        hyperparameters: Mapping[str, str] | None = None,
        spec: SampleSpecification | None = None,
    ) -> Result[PromotedModel | None, TrainingError]:
        """Run the full training pipeline.

        Args:
            parent_model: The base model to fine-tune from.
            page_images: Map of page number → raw image bytes for cropping.
            output_dir: Directory for intermediate and output artifacts.
            hyperparameters: Training hyperparameters passed to the trainer.
            spec: Sample eligibility specification.

        Returns:
            A promoted model if training succeeded and the candidate beat its
            parent, ``None`` if the candidate was refused promotion (with a
            logged reason), or an error.
        """
        run_id = f"train-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        params = dict(hyperparameters or {})

        # Stage 1: Collect accepted corrections
        corrections = self._correction_store.all_accepted()
        if not corrections:
            _LOG.warning("no accepted corrections available for training")
            return Ok(None)

        _LOG.info("training_start (%s) with %d corrections", run_id, len(corrections))

        # Stage 2: Assemble line-crop training samples (fixes D9)
        samples_result = assemble_training_samples(
            corrections=corrections,
            cropper=self._cropper,
            page_images=page_images,
            output_dir=output_dir / "samples",
            spec=spec or SampleSpecification(),
        )
        if isinstance(samples_result, Err):
            return Err(samples_result.error)
        samples = samples_result.value

        if not samples:
            _LOG.warning("no training samples after filtering")
            return Ok(None)

        # Stage 3: Export training data to trainer format
        export_result = self._exporter.export(samples, output_dir / "training_data")
        if isinstance(export_result, Err):
            return Err(export_result.error)
        data_path = export_result.value

        # Stage 4: Train the model
        train_result = self._trainer.train(
            data=data_path,
            parent=parent_model,
            params=params,
        )
        if isinstance(train_result, Err):
            return Err(train_result.error)
        candidate = train_result.value
        if candidate is None:
            return Err(TrainingError("trainer returned no candidate"))

        # Stage 5 & 6: Evaluate candidate and parent
        candidate_eval = self._evaluator.evaluate(
            _CandidateEngine(candidate),
            SplitName.TEST,
        )
        if isinstance(candidate_eval, Err):
            return Err(candidate_eval.error)

        parent_eval = self._evaluator.evaluate(
            _ParentEngine(parent_model),
            SplitName.TEST,
        )
        if isinstance(parent_eval, Err):
            return Err(parent_eval.error)

        # Stage 7: Promotion policy decides
        # Both are Ok here — the Err branches returned above. Substituting a
        # zeroed EvaluationReport instead (as this once did) would feed the
        # promotion policy fabricated CERs and could promote a model that was
        # never actually evaluated.
        candidate_report = candidate_eval.value
        parent_report = parent_eval.value

        decision = self._promotion_policy.decide(
            candidate=candidate,
            candidate_report=candidate_report,
            parent_report=parent_report,
        )
        if isinstance(decision, Err):
            _LOG.warning(
                "promotion_refused (%s): %s",
                run_id,
                str(decision.error),
            )
            return Ok(None)

        promoted = decision.value

        # Stage 8: Register the promoted model
        register_result = self._model_registry.register(promoted)
        if isinstance(register_result, Err):
            return Err(register_result.error)

        _LOG.info(
            "promotion_success (%s) improvement=%.2f%%",
            run_id,
            promoted.improvement_pct,
        )

        if self._event_bus is not None:
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class _PromotionEvent:
                event_type: str
                run_id: str
                model_hash: str
                improvement_pct: float

            self._event_bus.publish(
                _PromotionEvent(
                    event_type="model_promoted",
                    run_id=run_id,
                    model_hash=promoted.model_ref.model_hash,
                    improvement_pct=promoted.improvement_pct,
                )
            )

        return Ok(promoted)


class _CandidateEngine:
    """Minimal IOCREngine-like wrapper for a ModelCandidate used in evaluation."""

    def __init__(self, candidate: ModelCandidate) -> None:
        self.candidate = candidate
        self.name = f"kraken:{candidate.path}"

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        """Placeholder — evaluation uses the real engine, not this wrapper."""
        raise NotImplementedError("_CandidateEngine is an evaluation placeholder")


class _ParentEngine:
    """Minimal IOCREngine-like wrapper for a parent ModelRef used in evaluation."""

    def __init__(self, parent: ModelRef) -> None:
        self.parent = parent
        self.name = f"kraken:{parent.model_name}"

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        """Placeholder — evaluation uses the real engine, not this wrapper."""
        raise NotImplementedError("_ParentEngine is an evaluation placeholder")


__all__ = ["TrainingOrchestrator"]
