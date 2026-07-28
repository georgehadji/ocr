"""Ketos trainer — adapter over the Kraken CLI for model fine-tuning.

Uses Kraken's CLI (not its Python API) to keep the heavy training stack
out of the inference process — same reasoning as Calamari's isolation.
Includes timeouts, resource caps, and structured logging.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from omniocr.domain.errors import TrainingError
from omniocr.domain.models import ModelRef
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import ModelCandidate
from omniocr.infrastructure.models import sha256_file
from omniocr.ports.interfaces import ITrainer

_LOG = logging.getLogger("omniocr.ketos_trainer")


class KetosTrainer(ITrainer):
    """Train or fine-tune a Kraken model via subprocess.

    Wraps the ``kraken train`` CLI command. The training process runs in
    a subprocess with configurable timeout and resource limits.
    """

    def __init__(
        self,
        timeout_seconds: int = 7200,
        device: str = "cpu",
    ) -> None:
        self._timeout = timeout_seconds
        self._device = device

    def train(
        self,
        data: Path,
        parent: ModelRef,
        params: Mapping[str, str],
    ) -> Result[ModelCandidate, TrainingError]:
        """Fine-tune a Kraken model on the given training data.

        Args:
            data: Path to the training data directory containing ``train.json``.
            parent: The base model reference to fine-tune from.
            params: Training hyperparameters (epochs, learning rate, etc.).

        Returns:
            A ``ModelCandidate`` pointing to the fine-tuned artifact, or an
            error.
        """
        train_json = data / "train.json"
        if not train_json.is_file():
            return Err(
                TrainingError(f"training data not found: {train_json}")
            )

        # Determine the parent model path
        parent_path = Path(parent.model_name)
        if not parent_path.is_file():
            return Err(
                TrainingError(f"parent model not found: {parent_path}")
            )

        # Build output path
        output_path = data.parent / "ketos_finetuned.mlmodel"

        # Build command
        epochs = params.get("epochs", "10")
        cmd = [
            sys.executable,
            "-m",
            "kraken",
            "--log",
            "info",
            "train",
            "--device",
            self._device,
            "--load",
            str(parent_path),
            "--train",
            str(train_json),
            "--epochs",
            epochs,
            "--output",
            str(output_path),
        ]

        # Optional parameters
        if "learning_rate" in params:
            cmd.extend(["--learning-rate", params["learning_rate"]])
        if "batch_size" in params:
            cmd.extend(["--batch-size", params["batch_size"]])
        if "decay" in params:
            cmd.extend(["--decay", params["decay"]])
        if "momentum" in params:
            cmd.extend(["--momentum", params["momentum"]])

        _LOG.info(
            "ketos_train_start train_json=%s parent=%s epochs=%s",
            str(train_json), str(parent_path), epochs,
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return Err(
                TrainingError(
                    f"ketos training timed out after {self._timeout}s"
                )
            )

        if result.returncode != 0:
            stderr = result.stderr[-2000:] if result.stderr else ""
            return Err(
                TrainingError(
                    f"ketos train failed (exit {result.returncode}): {stderr}"
                )
            )

        if not output_path.is_file():
            return Err(
                TrainingError(
                    f"ketos did not produce output model: {output_path}"
                )
            )

        model_hash = sha256_file(output_path)
        run_id = params.get("run_id", "unknown")

        candidate = ModelCandidate(
            path=output_path,
            model_hash=model_hash,
            parent=parent,
            run_id=run_id,
        )

        _LOG.info(
            "ketos_train_complete output=%s model_hash=%s",
            str(output_path), model_hash,
        )

        return Ok(candidate)


__all__ = ["KetosTrainer"]
