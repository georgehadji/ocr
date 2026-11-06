"""Prometheus metrics: what the API and the workers count, and what ``/metrics`` serves."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("prometheus_client")

from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import values  # noqa: E402

from editions.common.api import create_app  # noqa: E402
from editions.common.worker import run_job  # noqa: E402
from omniocr.domain.models import (  # noqa: E402
    DocumentPage,
    DocumentStructure,
    PageFailure,
    PipelineEvent,
    TenantContext,
)
from omniocr.domain.result import Ok  # noqa: E402
from omniocr.infrastructure.config import Settings  # noqa: E402
from omniocr.infrastructure.prometheus_metrics import (  # noqa: E402
    MULTIPROC_ENV,
    ApiMetrics,
    PageMetricsBus,
    WorkerMetrics,
    render,
)
from omniocr.ports.interfaces import IJobQueue, JobState  # noqa: E402

KEY = {"Authorization": "Bearer key-a"}
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10
_CONTEXT = TenantContext(organization_id="t", user_id="u", subscription_tier="test")


class _Queue(IJobQueue):
    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None:
        self.job_id = job_id

    def state(self, job_id: str) -> JobState:
        return JobState.QUEUED


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        upload_dir=tmp_path / "uploads",
        result_dir=tmp_path / "results",
        api_keys=(("key-a", "tenant-a"),),
        allowed_hosts=("testserver",),
    )
    return TestClient(create_app("test", _Queue(), settings))


def _scrape(client: TestClient) -> str:
    response = client.get("/metrics", headers=KEY)
    assert response.status_code == 200
    return response.text


def test_metrics_needs_an_api_key(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_api_counts_requests_by_route_template_and_accepted_jobs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    submitted = client.post("/ocr/submit", headers=KEY, files={"file": ("p.png", PNG)})
    job_id = submitted.json()["job_id"]
    client.get(f"/ocr/status/{job_id}", headers=KEY)
    client.get("/no-such-route")

    body = _scrape(client)

    assert "omniocr_jobs_submitted_total 1.0" in body
    assert 'omniocr_http_requests_total{method="POST",route="/ocr/submit",status="202"} 1.0' in body
    assert 'route="/ocr/status/{job_id}",status="200"' in body
    assert 'route="unmatched",status="404"' in body
    assert job_id not in body, "a job id in a label would make one series per job"
    assert "tenant-a" not in body


def test_a_rejected_upload_is_not_counted_as_a_submitted_job(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post("/ocr/submit", headers=KEY, files={"file": ("p.exe", b"MZ" + b"0" * 20)})

    body = _scrape(client)

    assert "omniocr_jobs_submitted_total 0.0" in body
    assert 'route="/ocr/submit",status="400"' in body


def test_page_events_become_page_counts_and_durations() -> None:
    metrics = WorkerMetrics()
    bus = PageMetricsBus(metrics)

    bus.publish(PipelineEvent("page_completed", 1, duration_ms=2500))
    bus.publish(PipelineEvent("page_failed", 2, "boom", 500))
    bus.publish(PipelineEvent("something_else", 3, duration_ms=9000))
    bus.publish("not an event")

    body = render(metrics.registry).decode()
    assert 'omniocr_pages_total{outcome="completed"} 1.0' in body
    assert 'omniocr_pages_total{outcome="failed"} 1.0' in body
    assert "omniocr_page_duration_seconds_count 2.0" in body
    assert "omniocr_page_duration_seconds_sum 3.0" in body


def _page(number: int, *, error: str | None) -> DocumentPage:
    failures = (PageFailure(error_type=error, message="x"),) if error else ()
    return DocumentPage(number=number, width=1, height=1, lines=(), failures=failures)


class _Pipeline:
    def __init__(self, pages: tuple[DocumentPage, ...]) -> None:
        self._pages = pages

    def run(self, data: bytes, context: TenantContext) -> Ok[DocumentStructure]:
        return Ok(DocumentStructure(pages=self._pages))

    def export(self, document: DocumentStructure, context: TenantContext) -> Ok[bytes]:
        return Ok(b"exported")


def _run(
    tmp_path: Path, pages: tuple[DocumentPage, ...], metrics: WorkerMetrics | None = None
) -> None:
    upload = tmp_path / "in.png"
    upload.write_bytes(b"x")
    run_job(upload, tmp_path / "out.md", _Pipeline(pages), _CONTEXT, metrics)  # type: ignore[arg-type]


def test_a_finished_job_counts_its_outcome_and_its_page_failures(tmp_path: Path) -> None:
    metrics = WorkerMetrics()

    _run(tmp_path, (_page(1, error=None), _page(2, error="EmptyPage")), metrics)

    body = render(metrics.registry).decode()
    assert 'omniocr_jobs_total{outcome="done"} 1.0' in body
    assert 'omniocr_page_failures_total{error_type="EmptyPage"} 1.0' in body
    assert "omniocr_job_duration_seconds_count 1.0" in body


def test_a_job_where_every_page_failed_counts_as_failed(tmp_path: Path) -> None:
    metrics = WorkerMetrics()

    with pytest.raises(RuntimeError):
        _run(tmp_path, (_page(1, error="EngineError"),), metrics)

    body = render(metrics.registry).decode()
    assert 'omniocr_jobs_total{outcome="failed"} 1.0' in body
    assert 'omniocr_page_failures_total{error_type="EngineError"} 1.0' in body


def test_run_job_without_metrics_is_unchanged(tmp_path: Path) -> None:
    _run(tmp_path, ())

    assert (tmp_path / "out.md").read_bytes() == b"exported"


def test_the_scrape_adds_up_what_the_api_and_a_worker_wrote_to_the_shared_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared = tmp_path / "shared"
    monkeypatch.setenv(MULTIPROC_ENV, str(shared))
    # configure_process replaces the module-wide value class; put it back afterwards.
    monkeypatch.setattr(values, "ValueClass", values.ValueClass)
    api = ApiMetrics()
    worker = WorkerMetrics()

    api.jobs_submitted.inc()
    worker.pages.labels(outcome="completed").inc()

    body = render(api.registry).decode()
    assert "omniocr_jobs_submitted_total 1.0" in body
    assert 'omniocr_pages_total{outcome="completed"} 1.0' in body
    names = [p.name for p in shared.iterdir()]
    assert any("api-" in name for name in names), "files are named by role, not just pid"
    assert any("worker-" in name for name in names)
