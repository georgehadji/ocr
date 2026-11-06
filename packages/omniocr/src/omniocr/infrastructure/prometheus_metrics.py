"""Prometheus metrics for the server and cloud editions.

The API and the OCR workers are different processes, in different containers.
Each records what it sees; ``/metrics`` on the API serves both. That works
through ``prometheus_client``'s multiprocess mode: every process writes its
counters to files in ``PROMETHEUS_MULTIPROC_DIR`` (a volume the API and the
workers share) and a scrape adds them up.

Only counters and histograms are used: a gauge would need to be cleaned up when
its process dies. Without ``PROMETHEUS_MULTIPROC_DIR`` each process keeps its
own registry, which is right for tests and a single-process API, but then
``/metrics`` shows no worker numbers.

Desktop never imports this module, so it stays free of ``prometheus_client``.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, values
from prometheus_client.multiprocess import MultiProcessCollector

from omniocr.domain.models import DocumentPage, PipelineEvent

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

# A page takes seconds to minutes.
_PAGE_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
# A job is a whole book: minutes to hours.
_JOB_BUCKETS = (10, 30, 60, 300, 900, 1800, 3600, 7200, 14400, 28800)


def configure_process(role: str) -> None:
    """Make this process write its metrics under a name that is unique across containers.

    ``prometheus_client`` names a process's file after its pid. The API and the
    workers run in separate containers on one shared directory, and pids
    restart from the same small numbers in each, so two processes could end up
    writing the same file. The role and host go in front of the pid.

    shortcut: one small file set per worker process, kept after it exits so its
    counts survive. An RQ work horse is one process per job, so the directory
    grows with the number of jobs; a book takes hours, so that is slow. Empty
    the volume (``docker compose down -v``) if it ever matters.
    """
    directory = os.environ.get(MULTIPROC_ENV)
    if not directory:
        return
    Path(directory).mkdir(parents=True, exist_ok=True)
    host = socket.gethostname()
    # prometheus_client ships no annotations for this factory.
    values.ValueClass = values.MultiProcessValue(  # type: ignore[no-untyped-call]
        process_identifier=lambda: f"{role}-{host}-{os.getpid()}"
    )


def render(local: CollectorRegistry) -> bytes:
    """The exposition text: every process's numbers when multiprocess, else ``local``'s."""
    if os.environ.get(MULTIPROC_ENV):
        merged = CollectorRegistry()
        MultiProcessCollector(merged)  # type: ignore[no-untyped-call]
        return generate_latest(merged)
    return generate_latest(local)


class ApiMetrics:
    """What the API process counts: requests and accepted jobs."""

    def __init__(self) -> None:
        configure_process("api")
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "omniocr_http_requests_total",
            "HTTP requests by route template and status",
            ("method", "route", "status"),
            registry=self.registry,
        )
        self.jobs_submitted = Counter(
            "omniocr_jobs_submitted_total",
            "Uploads accepted and queued",
            registry=self.registry,
        )


class WorkerMetrics:
    """What an OCR worker counts: pages, page failures and whole jobs."""

    def __init__(self) -> None:
        configure_process("worker")
        self.registry = CollectorRegistry()
        self.pages = Counter(
            "omniocr_pages_total",
            "Pages that reached a terminal state",
            ("outcome",),
            registry=self.registry,
        )
        self.page_seconds = Histogram(
            "omniocr_page_duration_seconds",
            "Time to recognize one page",
            buckets=_PAGE_BUCKETS,
            registry=self.registry,
        )
        self.page_failures = Counter(
            "omniocr_page_failures_total",
            "Failures recorded on pages, by exception type",
            ("error_type",),
            registry=self.registry,
        )
        self.jobs = Counter(
            "omniocr_jobs_total",
            "Jobs a worker finished",
            ("outcome",),
            registry=self.registry,
        )
        self.job_seconds = Histogram(
            "omniocr_job_duration_seconds",
            "Time a worker spent on one job",
            buckets=_JOB_BUCKETS,
            registry=self.registry,
        )

    def record_failures(self, pages: Iterable[DocumentPage]) -> None:
        for page in pages:
            for failure in page.failures:
                self.page_failures.labels(error_type=failure.error_type).inc()

    def record_job(self, outcome: str, seconds: float) -> None:
        self.jobs.labels(outcome=outcome).inc()
        self.job_seconds.observe(seconds)


@lru_cache(maxsize=1)
def worker_metrics() -> WorkerMetrics:
    """The one ``WorkerMetrics`` of this process (a worker child builds its own after a fork)."""
    return WorkerMetrics()


class PageMetricsBus:
    """An ``IEventBus`` that turns the pipeline's page events into metrics."""

    _OUTCOMES = {"page_completed": "completed", "page_failed": "failed"}

    def __init__(self, metrics: WorkerMetrics) -> None:
        self._metrics = metrics

    def publish(self, event: object) -> None:
        if not isinstance(event, PipelineEvent):
            return
        outcome = self._OUTCOMES.get(event.event_type)
        if outcome is None:
            return
        self._metrics.pages.labels(outcome=outcome).inc()
        self._metrics.page_seconds.observe(event.duration_ms / 1000)


__all__ = [
    "MULTIPROC_ENV",
    "ApiMetrics",
    "PageMetricsBus",
    "WorkerMetrics",
    "configure_process",
    "render",
    "worker_metrics",
]
