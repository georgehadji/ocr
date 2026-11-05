"""File-backed registry of promoted models — what recognition reads.

Training promotes a candidate only after it beat its parent on the held-out
split. Without a durable record that promotion changed nothing: the in-memory
registry died with the process and MLflow was optional and write-only. This
registry copies the promoted weights into one directory, beside a
``promoted.json`` that says what each one was measured to do::

    models/promoted/
    ├── promoted.json
    └── 1291a7208643013a.mlmodel      # first 16 hex digits of its SHA-256

The newest promotion for a script wins. ``promoted_for_script`` re-hashes the
file, so a truncated or swapped weights file is ignored, not trusted.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omniocr.domain.errors import TrainingError
from omniocr.domain.models import ModelRef, Script
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import EvaluationReport, PromotedModel
from omniocr.infrastructure.models import sha256_file
from omniocr.ports.interfaces import IModelRegistry

_LOG = logging.getLogger("omniocr.model_registry")

INDEX_FILENAME = "promoted.json"


class FileModelRegistry(IModelRegistry):
    """Promoted models kept as plain files under ``directory``."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._index = self._dir / INDEX_FILENAME

    def register(self, model: PromotedModel) -> Result[None, TrainingError]:
        digest = model.model_ref.model_hash
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            weights = self._dir / f"{digest[:16]}.mlmodel"
            if not weights.is_file():
                shutil.copyfile(model.checkpoint, weights)
            if sha256_file(weights) != digest:
                weights.unlink(missing_ok=True)
                return Err(TrainingError(f"promoted weights do not match their hash {digest}"))
            records = self._read()
            records.append(
                {
                    "engine": model.model_ref.engine,
                    "file": weights.name,
                    "model_hash": digest,
                    "improvement_pct": model.improvement_pct,
                    "sample_count": model.report.sample_count,
                    "evaluated_on": model.report.evaluated_on,
                    "per_script_cer": {s.value: v for s, v in model.report.per_script_cer.items()},
                    "per_script_wer": {s.value: v for s, v in model.report.per_script_wer.items()},
                    "registered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            # Write beside the index, then swap: a crash must not leave a torn index.
            partial = self._index.with_name(INDEX_FILENAME + ".part")
            partial.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(partial, self._index)
        except OSError as exc:
            return Err(TrainingError(f"could not record the promoted model: {exc}"))
        return Ok(None)

    def promoted_for_script(self, script: Script) -> PromotedModel | None:
        for record in reversed(self._read()):
            if script.value not in record.get("per_script_cer", {}):
                continue
            weights = self._dir / str(record.get("file", ""))
            if not weights.is_file() or sha256_file(weights) != record.get("model_hash"):
                _LOG.warning("ignoring promoted model %s: missing or altered", weights)
                continue
            return self._promoted(record, weights)
        return None

    def promoted_for_typeface(self, typeface: str) -> PromotedModel | None:
        # A promotion is measured per script; no typeface is recorded with it.
        return None

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    @staticmethod
    def _promoted(record: dict[str, Any], weights: Path) -> PromotedModel:
        return PromotedModel(
            model_ref=ModelRef(
                engine=str(record["engine"]),
                model_name=str(weights),
                model_hash=str(record["model_hash"]),
            ),
            report=EvaluationReport(
                model_hash=str(record["model_hash"]),
                per_script_cer={Script(k): float(v) for k, v in record["per_script_cer"].items()},
                per_script_wer={Script(k): float(v) for k, v in record["per_script_wer"].items()},
                sample_count=int(record["sample_count"]),
                evaluated_on=str(record["evaluated_on"]),
            ),
            improvement_pct=float(record["improvement_pct"]),
            checkpoint=weights,
        )


__all__ = ["FileModelRegistry"]
