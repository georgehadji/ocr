"""``run_job``: what the queue records for a finished upload."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.errors import ExportError, IngestError
from omniocr.domain.models import (
    DocumentPage,
    DocumentStructure,
    PageFailure,
    PipelineStage,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result

from editions.common.worker import JobFailed, run_job

_CONTEXT = TenantContext(organization_id="t", user_id="u", subscription_tier="test")

# What an unanticipated exception's text can carry: a server path.
_SECRET = "[Errno 2] No such file or directory: '/opt/omniocr/models/x.mlmodel'"


def _page(
    number: int, *, failed: bool, message: str = "boom", stage: PipelineStage | None = None
) -> DocumentPage:
    failures = (PageFailure("EngineError", message, stage),) if failed else ()
    return DocumentPage(number=number, width=1, height=1, lines=(), failures=failures)


class _Pipeline:
    def __init__(
        self,
        pages: tuple[DocumentPage, ...] = (),
        run_error: Exception | None = None,
        export_error: Exception | None = None,
    ) -> None:
        self._pages = pages
        self._run_error = run_error
        self._export_error = export_error

    def run(self, data: bytes, context: TenantContext) -> Result[DocumentStructure, Exception]:
        if self._run_error is not None:
            return Err(self._run_error)
        return Ok(DocumentStructure(pages=self._pages))

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, Exception]:
        if self._export_error is not None:
            return Err(self._export_error)
        return Ok(b"exported")


def _run(tmp_path: Path, pages: tuple[DocumentPage, ...]) -> tuple[Path, Path]:
    upload, out = tmp_path / "in.png", tmp_path / "results" / "out.md"
    upload.write_bytes(b"x")
    run_job(upload, out, _Pipeline(pages), _CONTEXT)  # type: ignore[arg-type]
    return upload, out


def _failed(tmp_path: Path, pipeline: _Pipeline) -> str:
    upload, out = tmp_path / "in.png", tmp_path / "results" / "out.md"
    upload.write_bytes(b"x")
    with pytest.raises(JobFailed) as raised:
        run_job(upload, out, pipeline, _CONTEXT)  # type: ignore[arg-type]
    assert not out.exists()
    assert not upload.exists()
    return str(raised.value)


def test_a_run_where_every_page_failed_is_a_failed_job(tmp_path: Path) -> None:
    message = _failed(tmp_path, _Pipeline((_page(1, failed=True), _page(2, failed=True))))

    assert message == "every page failed (2); first: EngineError"


def test_the_reason_names_the_stage_the_first_page_stopped_in(tmp_path: Path) -> None:
    pages = (_page(1, failed=True, stage=PipelineStage.RECOGNISE),)

    assert _failed(tmp_path, _Pipeline(pages)) == (
        "every page failed (1); first: EngineError during recognise"
    )


def test_a_page_failure_message_never_reaches_the_reason(tmp_path: Path) -> None:
    """The reason goes back to the API client; a page message is raw exception text."""
    pages = (_page(1, failed=True, message=_SECRET),)

    assert "/opt/omniocr" not in _failed(tmp_path, _Pipeline(pages))


def test_a_failed_run_is_named_by_its_error_type_only(tmp_path: Path) -> None:
    message = _failed(tmp_path, _Pipeline(run_error=IngestError(_SECRET)))

    assert message == "the document could not be read (IngestError)"


def test_a_failed_export_is_named_by_its_error_type_only(tmp_path: Path) -> None:
    pipeline = _Pipeline((_page(1, failed=False),), export_error=ExportError(_SECRET))

    assert _failed(tmp_path, pipeline) == "export failed (ExportError)"


def test_the_raw_message_is_logged_on_the_server(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pages = (_page(1, failed=True, message=_SECRET),)

    with caplog.at_level("WARNING", logger="omniocr.worker"):
        _failed(tmp_path, _Pipeline(pages))

    assert _SECRET in caplog.text


def test_a_run_with_some_good_pages_still_writes_its_result(tmp_path: Path) -> None:
    upload, out = _run(tmp_path, (_page(1, failed=True), _page(2, failed=False)))

    assert out.read_bytes() == b"exported"
    assert not upload.exists()
