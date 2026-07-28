from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from omniocr.domain.errors import IngestError
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    EngineRun,
    ModelRef,
    OCRBlock,
    OCRLine,
    PageFailure,
    Script,
    Suggestion,
)
from omniocr.domain.result import Err, Ok, Result


class InMemoryJobStore:
    """Process-local checkpoint store for the desktop and test composition roots.

    A production server/cloud edition can implement the same port with SQLite,
    RQ, or a database without changing pipeline code.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, DocumentStructure] = {}

    def checkpoint(self, job_id: str, document: DocumentStructure) -> Result[None, IngestError]:
        if not job_id.strip():
            return Err(IngestError("job id must not be empty"))
        self._checkpoints[job_id] = document
        return Ok(None)

    def load(self, job_id: str) -> DocumentStructure | None:
        return self._checkpoints.get(job_id)


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _engine_run(data: dict[str, Any] | None) -> EngineRun | None:
    if data is None:
        return None
    return EngineRun(
        engine=data["engine"],
        model_ref=ModelRef(**data["model_ref"]),
        model_hash=data["model_hash"],
        params=tuple(data["params"]),
        timestamp=data["timestamp"],
    )


def _document_from_json(payload: str) -> DocumentStructure:
    data = json.loads(payload)
    pages: list[DocumentPage] = []
    for page_data in data["pages"]:
        lines: list[OCRLine] = []
        for line_data in page_data["lines"]:
            blocks = tuple(
                OCRBlock(
                    id=block["id"],
                    text=block["text"],
                    confidence=Confidence(block["confidence"]["value"]),
                    bbox=BBox(**block["bbox"]),
                    provenance=_engine_run(block["provenance"]),
                )
                for block in line_data["blocks"]
            )
            lines.append(
                OCRLine(
                    id=line_data["id"],
                    text=line_data["text"],
                    confidence=Confidence(line_data["confidence"]["value"]),
                    bbox=BBox(**line_data["bbox"]),
                    script=Script(line_data["script"]),
                    region_type=line_data.get("region_type", "unknown"),
                    reading_order=line_data.get("reading_order", 0),
                    blocks=blocks,
                    provenance=_engine_run(line_data["provenance"]),
                )
            )
        pages.append(
            DocumentPage(
                number=page_data["number"],
                width=page_data["width"],
                height=page_data["height"],
                lines=tuple(lines),
                suggestions=tuple(Suggestion(**item) for item in page_data["suggestions"]),
                failures=tuple(PageFailure(**item) for item in page_data.get("failures", ())),
            )
        )
    return DocumentStructure(pages=tuple(pages))


class SQLiteJobStore:
    """Durable checkpoint store using the Python standard-library SQLite driver."""

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS ocr_checkpoints "
            "(job_id TEXT PRIMARY KEY, document_json TEXT NOT NULL)"
        )
        self._connection.commit()

    def checkpoint(self, job_id: str, document: DocumentStructure) -> Result[None, IngestError]:
        if not job_id.strip():
            return Err(IngestError("job id must not be empty"))
        try:
            payload = json.dumps(asdict(document), default=_json_default, ensure_ascii=False)
            self._connection.execute(
                "INSERT INTO ocr_checkpoints(job_id, document_json) VALUES (?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET document_json=excluded.document_json",
                (job_id, payload),
            )
            self._connection.commit()
            return Ok(None)
        except sqlite3.Error as exc:
            return Err(IngestError(f"checkpoint failed: {exc}"))

    def load(self, job_id: str) -> DocumentStructure | None:
        row = self._connection.execute(
            "SELECT document_json FROM ocr_checkpoints WHERE job_id = ?", (job_id,)
        ).fetchone()
        return _document_from_json(row[0]) if row is not None else None

    def close(self) -> None:
        self._connection.close()


class RedisJobStore:
    """Checkpoint store backed by Redis for distributed Cloud deployments.

    Redis' built-in persistence (RDB/AOF) ensures durability across
    worker restarts. Each job is stored as a JSON string under the key
    ``omniocr:checkpoint:{job_id}``.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis as _redis

        self._redis = _redis.from_url(redis_url)

    def checkpoint(self, job_id: str, document: DocumentStructure) -> Result[None, IngestError]:
        if not job_id.strip():
            return Err(IngestError("job id must not be empty"))
        try:
            payload = json.dumps(asdict(document), default=_json_default, ensure_ascii=False)
            self._redis.set(f"omniocr:checkpoint:{job_id}", payload)
            return Ok(None)
        except Exception as exc:
            return Err(IngestError(f"Redis checkpoint failed: {exc}"))

    def load(self, job_id: str) -> DocumentStructure | None:
        try:
            raw = self._redis.get(f"omniocr:checkpoint:{job_id}")
            if raw is None:
                return None
            return _document_from_json(raw.decode("utf-8"))
        except Exception:
            return None


__all__ = ["InMemoryJobStore", "RedisJobStore", "SQLiteJobStore"]
