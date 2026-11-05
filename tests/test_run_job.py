"""``run_job``: what the queue records for a finished upload."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.models import (
    DocumentPage,
    DocumentStructure,
    PageFailure,
    TenantContext,
)
from omniocr.domain.result import Ok

from editions.common.worker import run_job

_CONTEXT = TenantContext(organization_id="t", user_id="u", subscription_tier="test")


def _page(number: int, *, failed: bool) -> DocumentPage:
    failures = (PageFailure(error_type="EngineError", message="boom"),) if failed else ()
    return DocumentPage(number=number, width=1, height=1, lines=(), failures=failures)


class _Pipeline:
    def __init__(self, pages: tuple[DocumentPage, ...]) -> None:
        self._pages = pages

    def run(self, data: bytes, context: TenantContext) -> Ok[DocumentStructure]:
        return Ok(DocumentStructure(pages=self._pages))

    def export(self, document: DocumentStructure, context: TenantContext) -> Ok[bytes]:
        return Ok(b"exported")


def _run(tmp_path: Path, pages: tuple[DocumentPage, ...]) -> tuple[Path, Path]:
    upload, out = tmp_path / "in.png", tmp_path / "results" / "out.md"
    upload.write_bytes(b"x")
    run_job(upload, out, _Pipeline(pages), _CONTEXT)  # type: ignore[arg-type]
    return upload, out


def test_a_run_where_every_page_failed_is_a_failed_job(tmp_path: Path) -> None:
    upload = tmp_path / "in.png"
    out = tmp_path / "results" / "out.md"
    upload.write_bytes(b"x")

    with pytest.raises(RuntimeError, match=r"every page failed \(2\); first: boom"):
        run_job(
            upload,
            out,
            _Pipeline((_page(1, failed=True), _page(2, failed=True))),  # type: ignore[arg-type]
            _CONTEXT,
        )

    assert not out.exists()
    assert not upload.exists()


def test_a_run_with_some_good_pages_still_writes_its_result(tmp_path: Path) -> None:
    upload, out = _run(tmp_path, (_page(1, failed=True), _page(2, failed=False)))

    assert out.read_bytes() == b"exported"
    assert not upload.exists()
