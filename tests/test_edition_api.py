"""The HTTP layer both editions share, run against an in-memory ``IJobQueue``.

The server (RQ) and cloud (Celery) editions differ only in their queue
adapter, so one module covers the routes for both.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from editions.common.api import create_app  # noqa: E402
from editions.common.worker import result_path, run_job  # noqa: E402
from omniocr.domain.errors import QueueError  # noqa: E402
from omniocr.domain.models import DocumentStructure, TenantContext  # noqa: E402
from omniocr.domain.result import Ok  # noqa: E402
from omniocr.infrastructure.config import Settings  # noqa: E402
from omniocr.ports.interfaces import IJobQueue, JobState  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10
KEY_A = {"Authorization": "Bearer key-a"}
KEY_B = {"Authorization": "Bearer key-b"}


class _Pipeline:
    """Echoes the tenant and the size of what it read, so results are distinguishable."""

    def run(self, data: bytes, context: TenantContext) -> Any:
        self._size = len(data)
        return Ok(DocumentStructure(pages=()))

    def export(self, document: object, context: TenantContext) -> Any:
        return Ok(f"{context.organization_id} read {self._size} bytes".encode())


class _FakeQueue(IJobQueue):
    """Runs each job at submit time with the real ``run_job``."""

    def __init__(self, settings: Settings, auto_run: bool = True) -> None:
        self._settings = settings
        self._auto_run = auto_run
        self.states: dict[str, JobState] = {}
        self.fail_submit = False
        self.fail_state = False

    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None:
        if self.fail_submit:
            raise QueueError("broker down")
        if not self._auto_run:
            self.states[job_id] = JobState.QUEUED
            return
        context = TenantContext(tenant, "worker", "server")
        run_job(upload, result_path(self._settings, job_id), _Pipeline(), context)  # type: ignore[arg-type]
        self.states[job_id] = JobState.DONE

    def state(self, job_id: str) -> JobState:
        if self.fail_state:
            raise QueueError("broker down")
        return self.states.get(job_id, JobState.NOT_FOUND)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "upload_dir": tmp_path / "uploads",
        "result_dir": tmp_path / "results",
        "api_keys": (("key-a", "tenant-a"), ("key-b", "tenant-b")),
        "allowed_hosts": ("testserver",),
        "max_upload_bytes": 1000,
    }
    return Settings(**{**values, **overrides})


def _client(tmp_path: Path, **kwargs: Any) -> tuple[TestClient, _FakeQueue, Settings]:
    settings = _settings(tmp_path)
    queue = _FakeQueue(settings, **kwargs)
    return TestClient(create_app("test", queue, settings)), queue, settings


def _submit(
    client: TestClient, headers: dict[str, str], name: str = "page.png", data: bytes = PNG
) -> Any:
    return client.post("/ocr/submit", headers=headers, files={"file": (name, data)})


def test_a_png_goes_from_submit_to_status_to_result(tmp_path: Path) -> None:
    client, _, settings = _client(tmp_path)

    submitted = _submit(client, KEY_A)
    job_id = submitted.json()["job_id"]

    assert submitted.status_code == 202
    assert len(job_id) == 32 and set(job_id) <= set("0123456789abcdef")
    assert client.get(f"/ocr/status/{job_id}", headers=KEY_A).json() == {
        "job_id": job_id,
        "status": "done",
    }
    result = client.get(f"/ocr/result/{job_id}", headers=KEY_A)
    assert result.status_code == 200
    assert result.text == f"tenant-a read {len(PNG)} bytes"
    assert list(settings.upload_dir.iterdir()) == [], "the worker deletes the upload"


def test_two_tenants_with_the_same_filename_get_separate_results(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    first = _submit(client, KEY_A).json()["job_id"]
    second = _submit(client, KEY_B).json()["job_id"]

    assert client.get(f"/ocr/result/{first}", headers=KEY_A).text.startswith("tenant-a")
    assert client.get(f"/ocr/result/{second}", headers=KEY_B).text.startswith("tenant-b")


def test_a_tenant_cannot_read_anothers_job(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    job_id = _submit(client, KEY_A).json()["job_id"]

    assert client.get(f"/ocr/status/{job_id}", headers=KEY_B).status_code == 404
    assert client.get(f"/ocr/result/{job_id}", headers=KEY_B).status_code == 404


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "key-a"}]
)
def test_a_missing_or_wrong_api_key_is_401(tmp_path: Path, headers: dict[str, str]) -> None:
    client, _, _ = _client(tmp_path)

    assert _submit(client, headers).status_code == 401
    assert client.get("/ocr/status/" + "a" * 32, headers=headers).status_code == 401
    assert client.get("/ocr/result/" + "a" * 32, headers=headers).status_code == 401


def test_no_configured_keys_refuses_everyone(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_keys=())
    client = TestClient(create_app("test", _FakeQueue(settings), settings))

    assert _submit(client, KEY_A).status_code == 401


def test_health_needs_no_key(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    assert client.get("/health").json()["status"] == "ok"


def test_an_oversized_upload_is_413_and_leaves_no_file(tmp_path: Path) -> None:
    client, _, settings = _client(tmp_path)

    response = _submit(client, KEY_A, data=PNG + b"x" * 2000)

    assert response.status_code == 413
    assert list(settings.upload_dir.iterdir()) == []


def test_the_real_filename_is_validated_once_at_submit(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    assert _submit(client, KEY_A, name="page.png", data=b"%PDF-1.4 not a png").status_code == 400
    assert _submit(client, KEY_A, name="notes.txt", data=PNG).status_code == 400
    assert _submit(client, KEY_A, data=b"").status_code == 400


def test_the_client_cannot_choose_the_job_id(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    response = client.post(
        "/ocr/submit",
        headers=KEY_A,
        files={"file": ("page.png", PNG)},
        data={"job_id": "../../etc/passwd"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] != "../../etc/passwd"


def test_a_queue_outage_at_submit_is_503_and_leaves_no_orphan(tmp_path: Path) -> None:
    client, queue, settings = _client(tmp_path)
    queue.fail_submit = True

    response = _submit(client, KEY_A)

    assert response.status_code == 503
    assert list(settings.upload_dir.iterdir()) == [], "the stored upload is removed"
    assert queue.states == {}


def test_a_queue_outage_on_status_is_503(tmp_path: Path) -> None:
    client, queue, _ = _client(tmp_path)
    queue.fail_state = True

    assert client.get("/ocr/status/" + "a" * 32, headers=KEY_A).status_code == 503


def test_unknown_and_malformed_ids_are_404(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    assert client.get("/ocr/status/" + "a" * 32, headers=KEY_A).status_code == 404
    assert client.get("/ocr/status/not-a-job-id", headers=KEY_A).status_code == 404
    assert client.get("/ocr/result/..%2Fsecret", headers=KEY_A).status_code == 404


def test_a_result_is_refused_until_the_job_is_done(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, auto_run=False)
    job_id = _submit(client, KEY_A).json()["job_id"]

    assert client.get(f"/ocr/status/{job_id}", headers=KEY_A).json()["status"] == "queued"
    assert client.get(f"/ocr/result/{job_id}", headers=KEY_A).status_code == 400


def test_cross_origin_requests_are_not_allowed_by_default(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_an_untrusted_host_is_refused(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)

    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400


# --- the queue adapters ------------------------------------------------------


def test_rq_adapter_turns_an_unreachable_redis_into_queue_error(tmp_path: Path) -> None:
    pytest.importorskip("rq")
    from redis import Redis

    from editions.server.queue import RqJobQueue

    queue = RqJobQueue(Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=1), 60, 60)

    with pytest.raises(QueueError):
        queue.submit("a" * 32, tmp_path / "x.png", "x.png", "tenant-a")
    with pytest.raises(QueueError):
        queue.state("a" * 32)


@pytest.mark.parametrize(
    ("celery_state", "expected"),
    [
        ("PENDING", JobState.QUEUED),
        ("STARTED", JobState.RUNNING),
        ("SUCCESS", JobState.DONE),
        ("FAILURE", JobState.FAILED),
    ],
)
def test_celery_adapter_maps_task_states(
    monkeypatch: pytest.MonkeyPatch, celery_state: str, expected: JobState
) -> None:
    pytest.importorskip("celery")
    from editions.cloud import queue as cloud_queue

    monkeypatch.setattr(
        cloud_queue, "AsyncResult", lambda job_id, app: SimpleNamespace(state=celery_state)
    )

    assert cloud_queue.CeleryJobQueue(object()).state("a" * 32) is expected  # type: ignore[arg-type]
