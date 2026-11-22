"""The OCR job lifecycle: how each queue backend's status becomes a ``JobStatus``.

``test_edition_api.py`` covers the HTTP routes against an in-memory queue; this
file covers the adapters and the lifecycle itself, which had no tests for the RQ
mapping at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("rq")
pytest.importorskip("celery")

from redis import Redis  # noqa: E402

from editions.cloud import queue as cloud_queue  # noqa: E402
from editions.common.worker import (  # noqa: E402
    JobFailed,
    failure_reason,
    pages_summary,
    upload_exists,
)
from editions.server import queue as server_queue  # noqa: E402
from omniocr.domain.result import Err, Ok  # noqa: E402
from omniocr.domain.state import transition  # noqa: E402
from omniocr.ports.interfaces import JOB_TRANSITIONS, JobState, JobStatus  # noqa: E402

JOB_ID = "0123456789abcdef-" + "a" * 32
_JOB_FAILED = f"{JobFailed.__module__}.{JobFailed.__qualname__}"


def _rq_queue(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    *,
    returned: object = None,
    exc_string: str = "",
) -> server_queue.RqJobQueue:
    """An RQ adapter whose Redis holds one job in ``status``."""
    job = SimpleNamespace(
        get_status=lambda: status,
        return_value=lambda: returned,
        latest_result=lambda: SimpleNamespace(exc_string=exc_string),
    )
    monkeypatch.setattr(server_queue, "Job", SimpleNamespace(fetch=lambda job_id, connection: job))
    return server_queue.RqJobQueue(Redis.from_url("redis://127.0.0.1:1/0"), 60, 60)


def _celery_queue(
    monkeypatch: pytest.MonkeyPatch, state: str, upload_dir: Path | None = None, **fields: Any
) -> Any:
    monkeypatch.setattr(
        cloud_queue,
        "AsyncResult",
        lambda job_id, app: SimpleNamespace(state=state, **fields),
    )
    return cloud_queue.CeleryJobQueue(object(), upload_dir)  # type: ignore[arg-type]


# --- what each backend status means (pinned before the lifecycle was declared) -------


@pytest.mark.parametrize(
    ("rq_status", "expected"),
    [
        ("queued", JobState.QUEUED),
        ("scheduled", JobState.QUEUED),
        ("deferred", JobState.QUEUED),
        ("started", JobState.RUNNING),
        ("finished", JobState.DONE),
        ("failed", JobState.FAILED),
        ("stopped", JobState.FAILED),
        ("canceled", JobState.FAILED),
    ],
)
def test_rq_adapter_maps_every_job_status(
    monkeypatch: pytest.MonkeyPatch, rq_status: str, expected: JobState
) -> None:
    assert _rq_queue(monkeypatch, rq_status).state(JOB_ID) is expected


@pytest.mark.parametrize(
    ("celery_state", "expected"),
    [
        ("PENDING", JobState.QUEUED),
        ("RECEIVED", JobState.QUEUED),
        ("STARTED", JobState.RUNNING),
        ("RETRY", JobState.RUNNING),
        ("SUCCESS", JobState.DONE),
        ("FAILURE", JobState.FAILED),
        ("REVOKED", JobState.FAILED),
        ("REJECTED", JobState.FAILED),
        ("IGNORED", JobState.FAILED),
    ],
)
def test_celery_adapter_maps_every_task_state(
    monkeypatch: pytest.MonkeyPatch, celery_state: str, expected: JobState
) -> None:
    assert _celery_queue(monkeypatch, celery_state).state(JOB_ID) is expected


def test_a_result_for_a_done_job_with_no_file_is_404(tmp_path: Path) -> None:
    """Pinned: the client is told the file is gone, not that the job is not done."""
    from test_edition_api import KEY_A, _client, _submit

    client, _, settings = _client(tmp_path)
    job_id = _submit(client, KEY_A).json()["job_id"]
    for result in settings.result_dir.iterdir():
        result.unlink()

    response = client.get(f"/ocr/result/{job_id}", headers=KEY_A)

    assert response.status_code == 404
    assert response.json()["detail"] == "Result file not found"


# --- the lifecycle itself ------------------------------------------------------------


def test_the_backend_moves_a_job_forward_only() -> None:
    table = JOB_TRANSITIONS
    assert transition("OCR job", table, JobState.QUEUED, JobState.RUNNING) == Ok(JobState.RUNNING)
    assert transition("OCR job", table, JobState.RUNNING, JobState.DONE) == Ok(JobState.DONE)
    assert isinstance(transition("OCR job", table, JobState.DONE, JobState.RUNNING), Err)
    assert isinstance(transition("OCR job", table, JobState.FAILED, JobState.QUEUED), Err)


def test_states_keep_the_strings_clients_already_see() -> None:
    assert [state.value for state in JobState] == [
        "queued",
        "running",
        "done",
        "failed",
        "not_found",
    ]
    assert f"{JobState.DONE}" == "done"


def test_a_queue_that_knows_only_the_state_still_has_a_status() -> None:
    from omniocr.ports.interfaces import IJobQueue

    class _Bare(IJobQueue):
        def state(self, job_id: str) -> JobState:
            return JobState.RUNNING

    assert _Bare().status(JOB_ID) == JobStatus(JobState.RUNNING)


# --- reasons and page counts from the backends ---------------------------------------


def test_rq_done_job_reports_how_many_pages_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _rq_queue(monkeypatch, "finished", returned={"pages_total": 10, "pages_failed": 3})

    assert queue.status(JOB_ID) == JobStatus(JobState.DONE, pages_total=10, pages_failed=3)


def test_rq_failed_job_quotes_a_worker_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = f"Traceback (most recent call last):\n  File x\n{_JOB_FAILED}: every page failed (2); first: boom\n"

    status = _rq_queue(monkeypatch, "failed", exc_string=trace).status(JOB_ID)

    assert status == JobStatus(JobState.FAILED, reason="every page failed (2); first: boom")


def test_rq_failed_job_hides_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = "Traceback ...\nFileNotFoundError: [Errno 2] /srv/uploads/secret.png\n"

    status = _rq_queue(monkeypatch, "failed", exc_string=trace).status(JOB_ID)

    assert status.reason == "job failed"


@pytest.mark.parametrize("rq_status", ["stopped", "canceled"])
def test_rq_stopped_and_canceled_jobs_say_so(
    monkeypatch: pytest.MonkeyPatch, rq_status: str
) -> None:
    assert _rq_queue(monkeypatch, rq_status).status(JOB_ID).reason == f"job {rq_status}"


def test_rq_unknown_status_fails_with_a_reason_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="omniocr.server.queue"):
        status = _rq_queue(monkeypatch, "weird").status(JOB_ID)

    assert status == JobStatus(JobState.FAILED, reason="unknown backend status 'weird'")
    assert "weird" in caplog.text


def test_celery_done_task_reports_how_many_pages_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _celery_queue(monkeypatch, "SUCCESS", result={"pages_total": 4, "pages_failed": 0})

    assert queue.status(JOB_ID) == JobStatus(JobState.DONE, pages_total=4, pages_failed=0)


def test_celery_failed_task_quotes_a_worker_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    status = _celery_queue(monkeypatch, "FAILURE", result=JobFailed("export broke\nsecond line"))

    assert status.status(JOB_ID) == JobStatus(JobState.FAILED, reason="export broke")


def test_celery_failed_task_hides_an_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _celery_queue(monkeypatch, "FAILURE", result=OSError("/srv/uploads/secret.png"))

    assert queue.status(JOB_ID).reason == "job failed"


def test_celery_revoked_task_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _celery_queue(monkeypatch, "REVOKED").status(JOB_ID).reason == "job revoked"


def test_celery_unknown_state_fails_with_a_reason_and_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="omniocr.cloud.queue"):
        status = _celery_queue(monkeypatch, "WEIRD").status(JOB_ID)

    assert status == JobStatus(JobState.FAILED, reason="unknown backend status 'WEIRD'")
    assert "WEIRD" in caplog.text


# --- a Celery id nobody queued is not "queued" ---------------------------------------


def test_celery_pending_with_no_upload_is_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = _celery_queue(monkeypatch, "PENDING", tmp_path)

    assert queue.state(JOB_ID) is JobState.NOT_FOUND


@pytest.mark.parametrize("upload_name", [f"{JOB_ID}.pdf", JOB_ID])
def test_celery_pending_with_its_upload_is_still_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, upload_name: str
) -> None:
    (tmp_path / upload_name).write_bytes(b"x")

    assert _celery_queue(monkeypatch, "PENDING", tmp_path).state(JOB_ID) is JobState.QUEUED


def test_celery_pending_without_an_upload_dir_stays_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers that do not pass the directory keep the old behaviour."""
    assert _celery_queue(monkeypatch, "PENDING").state(JOB_ID) is JobState.QUEUED


def test_only_pending_is_checked_against_the_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _celery_queue(monkeypatch, "STARTED", tmp_path).state(JOB_ID) is JobState.RUNNING


# --- the worker's side of the contract -----------------------------------------------


def test_failure_reason_quotes_only_the_first_line_and_cuts_it() -> None:
    assert failure_reason(f"{_JOB_FAILED}: {'x' * 500}") == "x" * 200
    assert failure_reason(None) == "job failed"
    assert failure_reason("") == "job failed"


def test_failure_reason_quotes_the_first_line_of_a_multi_line_message() -> None:
    """RQ's traceback ends with the message's last line, not the marker line."""
    trace = f"Traceback (most recent call last):\n  File x\n{_JOB_FAILED}: cannot write\nline 2\n"

    assert failure_reason(trace) == "cannot write"


def test_failure_reason_ignores_a_job_failed_that_was_not_the_final_error() -> None:
    """A later exception raised while handling a ``JobFailed`` is not client-safe."""
    trace = (
        "Traceback (most recent call last):\n  File x\n"
        f"{_JOB_FAILED}: safe text\n\n"
        "During handling of the above exception, another exception occurred:\n\n"
        "Traceback (most recent call last):\n  File y\n"
        "OSError: [Errno 28] No space left on device: '/srv/results/x.md'\n"
    )

    assert failure_reason(trace) == "job failed"


def test_pages_summary_accepts_only_two_integers() -> None:
    assert pages_summary({"pages_total": 2, "pages_failed": 1}) == (2, 1)
    assert pages_summary({"pages_total": 2}) == (None, None)
    assert pages_summary({"pages_total": "2", "pages_failed": 1}) == (None, None)
    assert pages_summary(None) == (None, None)


def test_upload_exists_sees_a_suffixed_and_an_unsuffixed_upload(tmp_path: Path) -> None:
    assert not upload_exists(tmp_path, JOB_ID)
    (tmp_path / f"{JOB_ID}.png").write_bytes(b"x")
    assert upload_exists(tmp_path, JOB_ID)


# --- the HTTP status route -----------------------------------------------------------


def _status(client: Any, job_id: str) -> dict[str, Any]:
    from test_edition_api import KEY_A

    body: dict[str, Any] = client.get(f"/ocr/status/{job_id}", headers=KEY_A).json()
    return body


def test_status_of_a_plain_job_has_no_extra_fields(tmp_path: Path) -> None:
    from test_edition_api import KEY_A, _client, _submit

    client, _, _ = _client(tmp_path)
    job_id = _submit(client, KEY_A).json()["job_id"]

    assert _status(client, job_id) == {"job_id": job_id, "status": "done"}


def test_status_carries_a_failed_jobs_reason(tmp_path: Path) -> None:
    from test_edition_api import KEY_A, _client, _submit

    client, queue, _ = _client(tmp_path, auto_run=False)
    job_id = _submit(client, KEY_A).json()["job_id"]
    queue.status = lambda internal_id: JobStatus(JobState.FAILED, reason="export broke")  # type: ignore[method-assign]

    assert _status(client, job_id) == {
        "job_id": job_id,
        "status": "failed",
        "reason": "export broke",
    }


def test_status_carries_the_page_counts_of_a_partial_result(tmp_path: Path) -> None:
    from test_edition_api import KEY_A, _client, _submit

    client, queue, _ = _client(tmp_path)
    job_id = _submit(client, KEY_A).json()["job_id"]
    queue.status = lambda internal_id: JobStatus(  # type: ignore[method-assign]
        JobState.DONE, pages_total=10, pages_failed=3
    )

    body = _status(client, job_id)

    assert body["status"] == "done"
    assert (body["pages_total"], body["pages_failed"]) == (10, 3)
    assert "reason" not in body


def test_status_of_a_done_job_with_no_result_file_says_so(tmp_path: Path) -> None:
    from test_edition_api import KEY_A, _client, _submit

    client, _, settings = _client(tmp_path)
    job_id = _submit(client, KEY_A).json()["job_id"]
    for result in settings.result_dir.iterdir():
        result.unlink()

    assert _status(client, job_id) == {
        "job_id": job_id,
        "status": "done",
        "reason": "result missing",
    }
