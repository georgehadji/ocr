"""Tests for infrastructure/corrections_store.py — SQLite append-only store."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.corrections import Correction
from omniocr.domain.models import BBox, Script
from omniocr.domain.result import Ok
from omniocr.infrastructure.corrections_store import SqliteCorrectionStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteCorrectionStore:
    return SqliteCorrectionStore(str(tmp_path / "test_corrections.db"))


def _correction(line_id: str = "line-1") -> Correction:
    return Correction(
        line_id=line_id,
        page_number=1,
        original_text="original",
        corrected_text="corrected",
        corrected_by="tester",
        corrected_at="2026-07-28T12:00:00",
        bbox=BBox(0, 0, 100, 20),
        script=Script.BYZANTINE,
        accepted=True,
    )


class TestSqliteCorrectionStore:
    def test_append_and_retrieve(self, store: SqliteCorrectionStore) -> None:
        c = _correction()
        result = store.append(c)
        assert isinstance(result, Ok)

        all_c = store.all_accepted()
        assert len(all_c) == 1
        assert all_c[0].corrected_text == "corrected"
        assert all_c[0].line_id == "line-1"

    def test_for_document(self, store: SqliteCorrectionStore) -> None:
        for i in range(3):
            store.append(_correction(line_id=f"doc-{i}"))
        results = store.for_document("doc-")
        assert len(results) == 3

    def test_all_accepted_excludes_rejected(self, store: SqliteCorrectionStore) -> None:
        rejected = Correction(
            line_id="rejected",
            page_number=1,
            original_text="orig",
            corrected_text="rejected text",
            corrected_by="tester",
            corrected_at="2026-07-28T12:00:00",
            bbox=BBox(0, 0, 10, 10),
            script=Script.MODERN,
            accepted=False,
        )
        store.append(_correction())
        store.append(rejected)
        all_c = store.all_accepted()
        assert len(all_c) == 1
        assert all_c[0].line_id == "line-1"

    def test_empty_store(self, store: SqliteCorrectionStore) -> None:
        assert store.all_accepted() == []
        assert store.for_document("nonexistent") == []

    def test_append_multiple_same_line(self, store: SqliteCorrectionStore) -> None:
        """Append-only: same line can have multiple corrections (audit trail)."""
        for i in range(3):
            store.append(
                Correction(
                    line_id="line-1",
                    page_number=1,
                    original_text="orig",
                    corrected_text=f"v{i}",
                    corrected_by="tester",
                    corrected_at=f"2026-07-28T12:0{i}:00",
                    bbox=BBox(0, 0, 10, 10),
                    script=Script.MODERN,
                )
            )
        all_c = store.for_document("line-1")
        assert len(all_c) == 3
        assert all_c[0].corrected_text == "v0"
        assert all_c[2].corrected_text == "v2"

    def test_preserves_script(self, store: SqliteCorrectionStore) -> None:
        store.append(_correction())
        all_c = store.all_accepted()
        assert all_c[0].script == Script.BYZANTINE
