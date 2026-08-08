"""Corrections store — an append-only repository for human-verified transcriptions.

Corrections are never updated in place; a revised transcription is a new
``Correction`` with a later timestamp. This preserves the audit trail
scholarship requires and mirrors v1's suggest-only stance.

Uses SQLite as the persistence backend.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from omniocr.domain.corrections import Correction
from omniocr.domain.errors import CorrectionStoreError, TrainingError
from omniocr.domain.models import BBox, Script
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ICorrectionStore


class SqliteCorrectionStore(ICorrectionStore):
    """Append-only SQLite-backed corrections repository.

    Schema::

        CREATE TABLE corrections (
            line_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            original_text TEXT NOT NULL,
            corrected_text TEXT NOT NULL,
            corrected_by TEXT NOT NULL,
            corrected_at TEXT NOT NULL,
            bbox_json TEXT NOT NULL,
            script TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 1
        );
    """

    def __init__(self, db_path: str | Path = "corrections.db") -> None:
        self._db_path = Path(db_path)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corrections (
                line_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                original_text TEXT NOT NULL,
                corrected_text TEXT NOT NULL,
                corrected_by TEXT NOT NULL,
                corrected_at TEXT NOT NULL,
                bbox_json TEXT NOT NULL,
                script TEXT NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self._conn.commit()

    def append(self, correction: Correction) -> Result[None, TrainingError]:
        """Persist a single correction to the store."""
        try:
            self._conn.execute(
                """
                INSERT INTO corrections
                    (line_id, page_number, original_text, corrected_text,
                     corrected_by, corrected_at, bbox_json, script, accepted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction.line_id,
                    correction.page_number,
                    correction.original_text,
                    correction.corrected_text,
                    correction.corrected_by,
                    correction.corrected_at,
                    json.dumps(
                        {
                            "x": correction.bbox.x,
                            "y": correction.bbox.y,
                            "w": correction.bbox.w,
                            "h": correction.bbox.h,
                        }
                    ),
                    correction.script.value,
                    1 if correction.accepted else 0,
                ),
            )
            self._conn.commit()
            return Ok(None)
        except sqlite3.Error as exc:
            return Err(CorrectionStoreError(f"SQLite append failed: {exc}"))

    def for_document(self, document_id: str) -> Sequence[Correction]:
        """Return all corrections for a given line_id prefix (used as document grouping)."""
        try:
            cursor = self._conn.execute(
                """
                SELECT line_id, page_number, original_text, corrected_text,
                       corrected_by, corrected_at, bbox_json, script, accepted
                FROM corrections WHERE line_id LIKE ?
                ORDER BY corrected_at
                """,
                (f"{document_id}%",),
            )
            return [self._row_to_correction(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def all_accepted(self) -> Sequence[Correction]:
        """Return all accepted corrections."""
        try:
            cursor = self._conn.execute(
                """
                SELECT line_id, page_number, original_text, corrected_text,
                       corrected_by, corrected_at, bbox_json, script, accepted
                FROM corrections WHERE accepted = 1
                ORDER BY corrected_at
                """
            )
            return [self._row_to_correction(row) for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @staticmethod
    def _row_to_correction(row: tuple[Any, ...]) -> Correction:
        """Convert a SQLite row to a ``Correction`` value object."""
        bbox_data = json.loads(row[6])
        return Correction(
            line_id=row[0],
            page_number=row[1],
            original_text=row[2],
            corrected_text=row[3],
            corrected_by=row[4],
            corrected_at=row[5],
            bbox=BBox(x=bbox_data["x"], y=bbox_data["y"], w=bbox_data["w"], h=bbox_data["h"]),
            script=Script(row[7]),
            accepted=bool(row[8]),
        )


__all__ = ["SqliteCorrectionStore"]
