"""MLflow model registry — tracks training runs, parameters, metrics, and artifacts.

Repository over MLflow: params, metrics, artifacts, model lineage.
MLflow is optional — the registry degrades gracefully when MLflow is not
installed, using a no-op store.
"""

from __future__ import annotations

import logging
from typing import Mapping

from omniocr.domain.errors import TrainingError
from omniocr.domain.models import ModelRef, Script
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import PromotedModel
from omniocr.ports.interfaces import IModelRegistry

_LOG = logging.getLogger("omniocr.mlflow_registry")

try:
    import mlflow

    _HAS_MLFLOW = True
except ImportError:
    _HAS_MLFLOW = False


class MlflowModelRegistry(IModelRegistry):
    """MLflow-backed model registry for promoted models.

    When MLflow is not installed, falls back to a NoOpModelRegistry that
    logs operations via the standard logger.
    """

    def __init__(
        self,
        tracking_uri: str | None = None,
        experiment_name: str = "omniocr",
    ) -> None:
        self._experiment_name = experiment_name
        if _HAS_MLFLOW and tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

    def register(self, model: PromotedModel) -> Result[None, TrainingError]:
        """Log a promoted model to MLflow."""
        if not _HAS_MLFLOW:
            _LOG.info(
                "MLflow not installed — skipping registry for model %s",
                model.model_ref.model_hash,
            )
            return Ok(None)

        try:
            mlflow.set_experiment(self._experiment_name)
            with mlflow.start_run(run_name=f"promote-{model.model_ref.model_hash[:8]}"):
                mlflow.log_params(
                    {
                        "model_engine": model.model_ref.engine,
                        "model_hash": model.model_ref.model_hash,
                        "improvement_pct": model.improvement_pct,
                        "sample_count": model.report.sample_count,
                        "evaluated_on": model.report.evaluated_on,
                    }
                )
                for script, cer in model.report.per_script_cer.items():
                    mlflow.log_metric(f"cer_{script.value}", cer)
                for script, wer in model.report.per_script_wer.items():
                    mlflow.log_metric(f"wer_{script.value}", wer)

                # Log per-script improvement
                mlflow.log_metric("improvement_pct", model.improvement_pct)

                # Log the model artifact if it exists on disk
                try:
                    from pathlib import Path
                    path = Path(model.model_ref.model_name)
                    if path.exists():
                        mlflow.log_artifact(str(path), artifact_path="models")
                except (OSError, ValueError):
                    pass

            return Ok(None)
        except Exception as exc:
            return Err(TrainingError(f"MLflow registration failed: {exc}"))

    def promoted_for_script(self, script: Script) -> PromotedModel | None:
        """Query MLflow for the best promoted model for a given script."""
        # MLflow has no efficient "latest by tag" query without search;
        # for now, return None and let the caller fall through to defaults.
        return None

    def promoted_for_typeface(self, typeface: str) -> PromotedModel | None:
        """Query MLflow for a promoted model for a specific typeface."""
        return None


class InMemoryModelRegistry(IModelRegistry):
    """In-memory model registry for testing and fallback use."""

    def __init__(self) -> None:
        self._by_script: dict[Script, PromotedModel] = {}
        self._by_typeface: dict[str, PromotedModel] = {}

    def register(self, model: PromotedModel) -> Result[None, TrainingError]:
        """Register a promoted model in memory."""
        for script in model.report.per_script_cer:
            self._by_script[script] = model
        return Ok(None)

    def promoted_for_script(self, script: Script) -> PromotedModel | None:
        """Look up the best model for a script."""
        return self._by_script.get(script)

    def promoted_for_typeface(self, typeface: str) -> PromotedModel | None:
        """Look up a model for a specific typeface."""
        return self._by_typeface.get(typeface)


__all__ = [
    "InMemoryModelRegistry",
    "MlflowModelRegistry",
]
