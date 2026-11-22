"""Where a page failed, typed failure kinds and events, and the CLI exit codes (Phase 3)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from omniocr.application.pipeline import InMemoryPage, NullRouter, PipelineOrchestrator
from omniocr.domain.errors import EngineError, IngestError, LayoutError
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    EventType,
    FailureKind,
    OCRBlock,
    OCRLine,
    PageFailure,
    PipelineEvent,
    PipelineStage,
    UnreadablePage,
)
from omniocr.domain.result import Err, Ok
from omniocr.interfaces import cli


class _Layout:
    def __init__(self, lines: int = 1, error: Exception | None = None) -> None:
        self._lines = lines
        self._error = error

    def segment(self, page, context):
        if self._error is not None:
            return Err(self._error)
        return Ok(
            tuple(
                OCRLine(f"line-{i}", "", Confidence(0), BBox(0, 10 * i, 50, 8))
                for i in range(self._lines)
            )
        )


class _Engine:
    name = "stub"

    def __init__(self, mode: str = "ok") -> None:
        self._mode = mode

    def extract(self, page, context):
        if self._mode == "err":
            return Err(EngineError("server down"))
        if self._mode == "raise":
            raise RuntimeError("engine crashed")
        return Ok((OCRBlock("b", "text", Confidence(90), BBox(0, 0, 50, 8)),))


class _Pages:
    def __init__(self, *pages) -> None:
        self._pages = pages

    def stream(self, document: bytes) -> Iterator:
        yield from self._pages


class _Raises:
    """A stage double that raises where a real one would fail."""

    def __init__(self, method: str, error: Exception) -> None:
        self._error = error
        setattr(self, method, self._raise)

    def _raise(self, *args, **kwargs):
        raise self._error


class _FailsOnPage:
    """A reconciler that raises for one page's lines only."""

    def __init__(self, page_number: int) -> None:
        self._prefix = f"p{page_number}-"

    def reconcile(self, candidates, context):
        if candidates[0].id.startswith(self._prefix):
            raise RuntimeError("vote broke")
        return Ok(candidates[0])


def _run(pages=None, engine=None, **kwargs) -> tuple[DocumentPage, ...]:
    pipeline = PipelineOrchestrator(
        page_source=_Pages(*(pages or [InMemoryPage(1, b"x", 60, 30)])),
        layout_analyzer=kwargs.pop("layout_analyzer", _Layout()),
        router=NullRouter(engine or _Engine()),
        **kwargs,
    )
    return tuple(pipeline.run(b"doc").value.pages)


def _failure(page: DocumentPage) -> PageFailure:
    assert len(page.failures) == 1, page.failures
    return page.failures[0]


# --- the stage is recorded where the page stopped --------------------------


def test_a_healthy_page_has_no_failure() -> None:
    assert not _run()[0].failures


def test_an_unreadable_page_failed_at_ingest() -> None:
    failure = _failure(_run([UnreadablePage(1, "bad xref")])[0])

    assert failure.stage is PipelineStage.INGEST
    assert failure.error_type == FailureKind.INGEST_ERROR


def test_a_failing_primary_preprocess_failed_at_preprocess() -> None:
    page = _run(image_processor=_Raises("process", IngestError("cannot decode")))[0]

    assert _failure(page).stage is PipelineStage.PREPROCESS
    assert _failure(page).error_type == "IngestError"


def test_a_layout_error_failed_at_segment() -> None:
    page = _run(layout_analyzer=_Layout(error=LayoutError("no layout")))[0]

    assert _failure(page).stage is PipelineStage.SEGMENT


def test_an_engine_that_raises_failed_at_recognise() -> None:
    failure = _failure(_run(engine=_Engine("raise"))[0])

    assert failure.stage is PipelineStage.RECOGNISE
    assert failure.error_type == "RuntimeError"  # the catch-all keeps the exception name


def test_every_engine_failing_is_an_engine_error_at_recognise() -> None:
    failure = _failure(_run(engine=_Engine("err"))[0])

    assert failure.error_type == FailureKind.ENGINE_ERROR
    assert failure.stage is PipelineStage.RECOGNISE


def test_a_reconciler_that_raises_failed_at_reconcile() -> None:
    page = _run(reconciler=_Raises("reconcile", RuntimeError("vote broke")))[0]

    assert _failure(page).stage is PipelineStage.RECONCILE


def test_a_post_corrector_that_raises_failed_at_post_correct() -> None:
    page = _run(post_corrector=_Raises("correct", RuntimeError("lexicon broke")))[0]

    assert _failure(page).stage is PipelineStage.POST_CORRECT


def test_a_page_with_no_lines_is_an_empty_page_at_segment() -> None:
    failure = _failure(_run(layout_analyzer=_Layout(lines=0))[0])

    assert failure.error_type == FailureKind.EMPTY_PAGE
    assert failure.stage is PipelineStage.SEGMENT


def test_a_stage_is_not_carried_to_the_next_page() -> None:
    pages = _run(
        [InMemoryPage(1, b"x", 60, 30), InMemoryPage(2, b"x", 60, 30)],
        reconciler=_FailsOnPage(1),
    )

    assert pages[0].failures[0].stage is PipelineStage.RECONCILE
    assert not pages[1].failures


# --- frozen strings stay frozen ---------------------------------------------


def test_failure_kinds_keep_their_wire_strings() -> None:
    assert [str(k) for k in FailureKind] == ["IngestError", "EmptyPage", "EngineError"]


def test_event_types_keep_their_wire_strings() -> None:
    assert [str(e) for e in EventType] == [
        "page_completed",
        "page_failed",
        "model_promoted",
        "training_refused",
        "training_no_corrections",
        "training_no_samples",
    ]


def test_pipeline_stages_name_the_stages_in_order() -> None:
    assert [str(s) for s in PipelineStage] == [
        "ingest",
        "preprocess",
        "segment",
        "recognise",
        "reconcile",
        "post_correct",
    ]


def test_a_failure_built_the_old_way_still_works() -> None:
    assert PageFailure(error_type="EngineError", message="boom").stage is None


def test_published_events_are_typed_and_still_equal_their_strings() -> None:
    class Bus:
        def __init__(self) -> None:
            self.events: list[PipelineEvent] = []

        def publish(self, event: PipelineEvent) -> None:
            self.events.append(event)

    bus = Bus()
    _run(event_bus=bus)
    _run(engine=_Engine("raise"), event_bus=bus)

    assert [e.event_type for e in bus.events] == [EventType.PAGE_COMPLETED, EventType.PAGE_FAILED]
    assert bus.events[0].event_type == "page_completed"


# --- metrics are keyed by the enum ------------------------------------------


def test_the_metrics_bus_counts_by_event_type() -> None:
    pytest.importorskip("prometheus_client")
    from omniocr.infrastructure.prometheus_metrics import PageMetricsBus, WorkerMetrics

    metrics = WorkerMetrics()
    bus = PageMetricsBus(metrics)

    bus.publish(PipelineEvent(EventType.PAGE_COMPLETED, 1, duration_ms=10))
    bus.publish(PipelineEvent(EventType.PAGE_FAILED, 2, duration_ms=10))
    bus.publish(PipelineEvent(EventType.PAGE_FAILED, 3, duration_ms=10))

    counted = {
        s.labels["outcome"]: s.value
        for family in metrics.registry.collect()
        for s in family.samples
        if s.name == "omniocr_pages_total"
    }
    assert counted == {"completed": 1.0, "failed": 2.0}


def test_the_failure_counter_label_is_still_the_error_type() -> None:
    pytest.importorskip("prometheus_client")
    from omniocr.infrastructure.prometheus_metrics import WorkerMetrics

    metrics = WorkerMetrics()
    page = DocumentPage(
        1, 10, 10, failures=(PageFailure("EngineError", "x", PipelineStage.RECOGNISE),)
    )
    metrics.record_failures([page])

    labels = [
        s.labels
        for family in metrics.registry.collect()
        for s in family.samples
        if s.name == "omniocr_page_failures_total"
    ]
    assert labels == [{"error_type": "EngineError"}]


# --- CLI: exit codes are a documented enum, --json names the stage ----------


def test_exit_codes_keep_their_numbers() -> None:
    assert [(c.name, int(c)) for c in cli.ExitCode] == [
        ("OK", 0),
        ("FAILURE", 1),
        ("USAGE", 2),
        ("ENVIRONMENT", 3),
    ]


def test_the_old_exit_constants_are_the_enum_members() -> None:
    assert cli.EXIT_OK is cli.ExitCode.OK
    assert cli.EXIT_FAILURE is cli.ExitCode.FAILURE
    assert cli.EXIT_USAGE is cli.ExitCode.USAGE
    assert cli.EXIT_ENVIRONMENT is cli.ExitCode.ENVIRONMENT


def _cli_run(tmp_path, capsys, monkeypatch, pages) -> dict:
    from test_cli import _StubOrchestrator

    source = tmp_path / "in.pdf"
    source.write_bytes(b"fake-pdf")
    stub = _StubOrchestrator(pages, Ok(b"ok"))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())
    cli.main(["run", str(source), "--out", str(tmp_path / "out.txt"), "--json"])
    return json.loads(capsys.readouterr().out)


def test_run_json_names_the_stage_of_a_failure(tmp_path, capsys, monkeypatch) -> None:
    from test_cli import _line, _page

    failed = _page(2, failures=(PageFailure("EngineError", "boom", PipelineStage.RECOGNISE),))
    payload = _cli_run(tmp_path, capsys, monkeypatch, [_page(1, lines=(_line("l", "ok"),)), failed])

    assert payload["failures"] == [
        {"page": 2, "type": "EngineError", "message": "boom", "stage": "recognise"}
    ]


def test_run_json_omits_the_stage_when_it_is_unknown(tmp_path, capsys, monkeypatch) -> None:
    """Additive only: a failure with no stage keeps its original three keys."""
    from test_cli import _line, _page

    failed = _page(2, failures=(PageFailure("EngineError", "boom"),))
    payload = _cli_run(tmp_path, capsys, monkeypatch, [_page(1, lines=(_line("l", "ok"),)), failed])

    assert payload["failures"] == [{"page": 2, "type": "EngineError", "message": "boom"}]


def test_run_still_recognises_an_empty_page_by_its_kind(tmp_path, capsys, monkeypatch) -> None:
    from test_cli import _line, _page

    empty = _page(2, failures=(PageFailure(FailureKind.EMPTY_PAGE, "no lines"),))
    payload = _cli_run(tmp_path, capsys, monkeypatch, [_page(1, lines=(_line("l", "ok"),)), empty])

    assert payload["empty_pages"] == [2]
    assert payload["failures"] == []
    assert payload["ok"] is True


def test_a_failure_describes_where_it_stopped() -> None:
    staged = PageFailure("EngineError", "boom", PipelineStage.SEGMENT)

    assert staged.describe() == "Failed during segment: boom"
    assert PageFailure("EngineError", "boom").describe() == "Failed: boom"
