"""Regression tests for defects that lived in the ungated edition trees.

`editions/` was outside every CI gate — no lint, no typing, no tests — until
the gate was widened. Each test below pins one bug that gap allowed to ship.
The editions need `celery`/`fastapi`, so those imports are guarded rather
than assumed.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from editions.common.worker import queue_id, result_path, run_job
from omniocr.domain.errors import ExportError
from omniocr.domain.models import DocumentStructure, TenantContext
from omniocr.domain.result import Err, Ok
from omniocr.infrastructure.config import Settings

CONTEXT = TenantContext("tenant-a", "worker", "server")


# --------------------------------------------------------------- task signatures


def test_celery_task_takes_a_path_not_the_document() -> None:
    """The queue carries the stored upload's path. Base64 of a 2 GB book in the
    broker message was the alternative, and the API had read it all into memory."""
    pytest.importorskip("celery")
    from editions.cloud.tasks import process_ocr

    parameters = [
        p for p in inspect.signature(process_ocr.run).parameters.values() if p.name != "self"
    ]

    assert [p.name for p in parameters] == ["job_id", "upload", "tenant"]
    assert "settings_json" not in inspect.signature(process_ocr.run).parameters


def test_celery_task_keeps_its_limit_below_the_broker_redelivery_timeout() -> None:
    """With acks_late a task still running past visibility_timeout is redelivered
    to a second worker, so a long book would be read twice."""
    pytest.importorskip("celery")
    from editions.cloud.celery_app import app

    visibility = app.conf.broker_transport_options["visibility_timeout"]

    assert app.conf.task_time_limit < visibility


def test_run_ocr_job_takes_the_stored_upload_not_bytes() -> None:
    from editions.server.composition import run_ocr_job

    assert list(inspect.signature(run_ocr_job).parameters) == ["job_id", "upload", "tenant"]


# ------------------------------------------------------------------- run_job


class _Pipeline:
    def __init__(self, run_result: Any = None, export_result: Any = None) -> None:
        self._run = run_result or Ok(DocumentStructure(pages=()))
        self._export = export_result or Ok(b"# done")

    def run(self, data: bytes, context: TenantContext) -> Any:
        return self._run

    def export(self, document: object, context: TenantContext) -> Any:
        return self._export


def test_run_job_writes_the_result_whole_and_deletes_the_upload(tmp_path: Path) -> None:
    upload = tmp_path / "in.png"
    upload.write_bytes(b"png")
    out = tmp_path / "results" / "job.md"

    run_job(upload, out, _Pipeline(), CONTEXT)  # type: ignore[arg-type]

    assert out.read_bytes() == b"# done"
    assert not upload.exists()
    assert not (tmp_path / "results" / "job.md.part").exists()


def test_a_failed_export_raises_and_leaves_no_result(tmp_path: Path) -> None:
    """A failed export used to become an empty .md reported as completed."""
    upload = tmp_path / "in.png"
    upload.write_bytes(b"png")
    out = tmp_path / "job.md"
    pipeline = _Pipeline(export_result=Err(ExportError("disk full")))

    with pytest.raises(RuntimeError, match="disk full"):
        run_job(upload, out, pipeline, CONTEXT)  # type: ignore[arg-type]

    assert not out.exists()
    assert not upload.exists(), "the upload is deleted even when the run fails"


def test_a_failed_run_raises(tmp_path: Path) -> None:
    upload = tmp_path / "in.png"
    upload.write_bytes(b"png")
    pipeline = _Pipeline(run_result=Err(ExportError("no engine")))

    with pytest.raises(RuntimeError, match="no engine"):
        run_job(upload, tmp_path / "job.md", pipeline, CONTEXT)  # type: ignore[arg-type]


def test_queue_ids_differ_per_tenant_for_the_same_job() -> None:
    job_id = "a" * 32

    assert queue_id("tenant-a", job_id) != queue_id("tenant-b", job_id)
    assert queue_id("tenant-a", job_id) == queue_id("tenant-a", job_id)


def test_result_paths_follow_the_queue_id(tmp_path: Path) -> None:
    settings = Settings(result_dir=tmp_path)

    assert result_path(settings, "x") == tmp_path / "x.md"


# ------------------------------------------------------------------- settings


def test_api_keys_parse_as_key_tenant_pairs() -> None:
    settings = Settings.from_env({"OMNIOCR_API_KEYS": "k1:acme, k2:globex"})

    assert settings.api_keys == (("k1", "acme"), ("k2", "globex"))
    assert "k1" not in repr(settings)


def test_a_malformed_api_key_entry_is_rejected() -> None:
    with pytest.raises(ValueError, match="key:tenant"):
        Settings.from_env({"OMNIOCR_API_KEYS": "no-tenant"})


def test_the_edition_defaults_are_same_origin_and_localhost() -> None:
    settings = Settings.from_env({})

    assert settings.cors_origins == ()
    assert settings.allowed_hosts == ("localhost", "127.0.0.1")
    assert settings.api_keys == (), "no keys configured means no access"
    assert settings.job_timeout_s == 6 * 3600
    assert settings.result_ttl_s == 7 * 24 * 3600
