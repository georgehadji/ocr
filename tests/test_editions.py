"""Regression tests for defects that lived in the ungated edition trees.

`editions/` was outside every CI gate — no lint, no typing, no tests — until
the gate was widened. Each test below pins one bug that gap allowed to ship.
The editions need `celery`/`fastapi`, so those imports are guarded rather
than assumed.
"""

from __future__ import annotations

import base64
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# --------------------------------------------------------------- cloud tasks


def test_celery_task_takes_base64_not_raw_bytes() -> None:
    """The Celery app sets ``task_serializer="json"``.

    Enqueuing raw ``bytes`` raises ``EncodeError`` in the *submitting* process,
    so every Cloud submit failed the moment Celery was importable — the worker
    never saw a job. The payload parameter must be a ``str``.
    """
    pytest.importorskip("celery")
    from editions.cloud.tasks import process_ocr

    parameters = [
        p for p in inspect.signature(process_ocr.run).parameters.values() if p.name != "self"
    ]

    assert [p.name for p in parameters] == ["document_b64"]
    assert parameters[0].annotation in (str, "str")


def test_celery_task_takes_no_settings_argument() -> None:
    """``settings_json`` was never read, and both callers passed a live
    ``Settings`` dataclass into it — which the JSON serializer also rejects.
    The worker's own environment is the authority on how it runs."""
    pytest.importorskip("celery")
    from editions.cloud.tasks import process_ocr

    assert "settings_json" not in inspect.signature(process_ocr.run).parameters


def test_celery_payload_round_trips_through_json() -> None:
    """The whole point of base64: the argument must survive JSON encoding."""
    document = b"\x89PNG\r\n\x1a\n\xff\xfe binary payload"
    encoded = base64.b64encode(document).decode("ascii")

    assert base64.b64decode(json.loads(json.dumps(encoded))) == document

    with pytest.raises(TypeError):
        json.dumps(document)  # the original defect, pinned


# ------------------------------------------------------------- server worker


def test_run_ocr_job_takes_only_the_document() -> None:
    """``run_ocr_job(job_data, settings_json)`` declared a ``str`` parameter it
    never read, and both call sites passed a ``Settings`` object into it."""
    from editions.server.composition import run_ocr_job

    assert list(inspect.signature(run_ocr_job).parameters) == ["job_data"]


# -------------------------------------------------------------- cloud routes


def _cloud_client() -> tuple[Any, Any]:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from editions.cloud import main

    main._jobs.clear()
    return TestClient(main.app), main


def test_status_does_not_report_an_unrelated_jobs_state() -> None:
    """The Celery lookup ran only when the job was *absent*, then picked
    ``next(j["task_id"] for j in _jobs.values())`` — an arbitrary *other* job —
    and returned a stranger's status under this job's id."""
    client, main = _cloud_client()
    main._jobs["default:mine"] = {"status": "completed", "filename": "mine.pdf"}
    main._jobs["default:theirs"] = {"status": "queued", "task_id": "celery-task-of-theirs"}

    payload = client.get("/ocr/status/mine").json()

    assert payload["job_id"] == "mine"
    assert payload["status"] == "completed"
    assert "celery-task-of-theirs" not in str(payload)


def test_status_404s_for_an_unknown_job() -> None:
    client, _ = _cloud_client()

    assert client.get("/ocr/status/nope").status_code == 404


def test_failed_export_is_reported_as_failed_not_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed export substituted ``b""``, wrote an empty ``.md``, and still
    reported "completed" — the caller downloaded an empty file believing the
    run had worked."""
    client, main = _cloud_client()

    from omniocr.domain.errors import ExportError
    from omniocr.domain.models import DocumentStructure
    from omniocr.domain.result import Err, Ok

    class _ExportFailsPipeline:
        def run(self, data: bytes, ctx: object) -> Any:
            return Ok(DocumentStructure(pages=()))

        def export(self, document: object, ctx: object) -> Any:
            return Err(ExportError("disk full"))

    # Force the synchronous fallback, then fail the export inside it.
    monkeypatch.setitem(sys.modules, "editions.cloud.tasks", None)
    monkeypatch.setattr(
        "editions.cloud.composition.create_cloud_pipeline",
        lambda settings=None: _ExportFailsPipeline(),
    )
    monkeypatch.chdir(tmp_path)

    response = client.post(
        "/ocr/submit", files={"file": ("a.pdf", b"%PDF-1.4 fake", "application/pdf")}
    )

    assert response.json()["status"] == "failed"
    assert not (tmp_path / "cloud_results").exists()
