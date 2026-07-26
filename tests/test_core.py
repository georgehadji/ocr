from __future__ import annotations

from omniocr.application.pipeline import (
    InMemoryPage,
    PipelineOrchestrator,
    SuggestOnlyCorrector,
    SetLexicon,
    build_document,
)
from omniocr.domain.models import (
    BBox,
    Confidence,
    OCRLine,
    PipelineEvent,
    Script,
    Suggestion,
    TenantContext,
)
from omniocr.domain.result import Err, Ok
from omniocr.infrastructure.preprocess import normalize_nfc
from omniocr.infrastructure.jobs import InMemoryJobStore, SQLiteJobStore
from omniocr.infrastructure.events import InMemoryEventBus


def test_bbox_and_confidence_validate() -> None:
    bbox = BBox(1, 2, 3, 4)
    assert bbox.right == 4
    assert bbox.bottom == 6
    assert Confidence(99.5).value == 99.5


def test_result_supports_map_and_bind() -> None:
    result = Ok(2).map(lambda value: value + 1).and_then(lambda value: Ok(value * 3))
    assert isinstance(result, Ok)
    assert result.value == 9
    err = Err("boom").map(lambda value: value + 1)
    assert isinstance(err, Err)


def test_normalize_nfc_is_idempotent() -> None:
    assert normalize_nfc("ἵ") == normalize_nfc(normalize_nfc("ἵ"))


def test_pipeline_default_is_ok_and_returns_document_structure() -> None:
    orchestrator = PipelineOrchestrator()
    result = orchestrator.run(b"")
    assert result.is_ok()
    assert len(result.value.pages) == 1
    assert result.value.pages[0].lines[0].script == Script.UNKNOWN
    assert result.value.pages[0].lines[0].bbox == BBox(0, 0, 1, 1)


def test_layout_analyzer_uses_page_geometry_and_script() -> None:
    from omniocr.application.pipeline import SingleLineLayoutAnalyzer

    analyzer = SingleLineLayoutAnalyzer(Script.POLYTONIC)
    result = analyzer.segment(InMemoryPage(2, b"page", 1200, 800), TenantContext("o", "u", "d"))

    assert result.is_ok()
    assert result.value[0].bbox == BBox(0, 0, 1200, 800)
    assert result.value[0].script == Script.POLYTONIC


def test_build_document_and_suggestion_are_immutable_layer() -> None:
    line = OCRLine(
        id="line-1",
        text="ἀνθρωπος",
        confidence=Confidence(87),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )
    document = build_document((line,))
    assert document.pages[0].lines[0].text == "ἀνθρωπος"
    suggestion = Suggestion(
        line_id=line.id,
        source_text=line.text,
        suggestion_text=normalize_nfc(line.text),
        reason="unicode_nfc",
    )
    assert suggestion.source_text == line.text


def test_nfc_corrector_only_emits_a_suggestion_when_text_changes() -> None:
    line = OCRLine(
        id="line-1",
        text="καί",
        confidence=Confidence(90),
        bbox=BBox(0, 0, 10, 10),
    )

    result = SuggestOnlyCorrector().correct(line, TenantContext("org", "user", "desktop"))

    assert result.is_ok()
    assert result.value == ()


def test_suggest_only_corrector_highlights_unknown_words_without_rewriting_source() -> None:
    source = "γνωστή άγνωστη"
    line = OCRLine(
        id="line-lexicon",
        text=source,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 10, 10),
        script=Script.PONTIAN,
    )

    result = SuggestOnlyCorrector({Script.PONTIAN: SetLexicon("pontian", ("γνωστή",))}).correct(
        line, TenantContext("org", "user", "desktop")
    )

    assert result.is_ok()
    assert line.text == source
    assert result.value[0].source_text == "άγνωστη"
    assert result.value[0].suggestion_text == "άγνωστη"
    assert result.value[0].reason == "not_in_pontian_lexicon"


def test_suggest_only_corrector_expands_configured_ligatures_reversibly() -> None:
    source = "ϗ λόγος"
    line = OCRLine(
        id="line-ligature",
        text=source,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 10, 10),
        script=Script.BYZANTINE,
    )

    result = SuggestOnlyCorrector({}).correct(
        line,
        TenantContext("org", "user", "desktop"),
    )
    configured = SuggestOnlyCorrector(ligatures={"ϗ": "και"}).correct(
        line,
        TenantContext("org", "user", "desktop"),
    )

    assert result.is_ok()
    assert result.value == ()
    assert configured.is_ok()
    assert configured.value[0].suggestion_text == "και λόγος"
    assert configured.value[0].reversible
    assert line.text == source


def test_pipeline_checkpoints_each_completed_page() -> None:
    class TwoPageSource:
        def stream(self, document: bytes):
            yield InMemoryPage(1, b"one", 10, 10)
            yield InMemoryPage(2, b"two", 20, 20)

    store = InMemoryJobStore()
    result = PipelineOrchestrator(page_source=TwoPageSource(), job_store=store).run(
        b"document", job_id="job-1"
    )

    assert result.is_ok()
    checkpoint = store.load("job-1")
    assert checkpoint is not None
    assert [page.number for page in checkpoint.pages] == [1, 2]
    assert [page.width for page in checkpoint.pages] == [10, 20]


def test_sqlite_job_store_round_trips_document() -> None:
    store = SQLiteJobStore(":memory:")
    original = build_document((), page_number=3, width=300, height=400)

    result = store.checkpoint("job-1", original)
    loaded = store.load("job-1")
    store.close()

    assert result.is_ok()
    assert loaded == original


def test_pipeline_resume_skips_completed_pages() -> None:
    class TwoPageSource:
        def __init__(self, pages: tuple[InMemoryPage, ...]) -> None:
            self.pages = pages

        def stream(self, document: bytes):
            for page in self.pages:
                yield page

    store = InMemoryJobStore()
    first_source = TwoPageSource((InMemoryPage(1, b"one", 10, 10),))
    first = PipelineOrchestrator(page_source=first_source, job_store=store).run(
        b"document", job_id="job-1"
    )
    assert first.is_ok()

    second_source = TwoPageSource(
        (InMemoryPage(1, b"one", 10, 10), InMemoryPage(2, b"two", 20, 20))
    )
    resumed = PipelineOrchestrator(page_source=second_source, job_store=store).run(
        b"document", resume_job_id="job-1"
    )

    assert resumed.is_ok()
    assert [page.number for page in resumed.value.pages] == [1, 2]
    assert [page.width for page in resumed.value.pages] == [10, 20]


def test_pipeline_keeps_failed_page_and_continues_to_later_pages() -> None:
    class FailingProcessor:
        def process(self, page, context):
            from omniocr.domain.errors import IngestError

            if page.number == 2:
                return Err(IngestError("unreadable page"))
            return Ok(page)

    class ThreePageSource:
        def stream(self, document: bytes):
            for number in (1, 2, 3):
                yield InMemoryPage(number, str(number).encode(), 10, 10)

    result = PipelineOrchestrator(
        page_source=ThreePageSource(), image_processor=FailingProcessor()
    ).run(b"document")

    assert result.is_ok()
    assert [page.number for page in result.value.pages] == [1, 2, 3]
    assert result.value.pages[1].failures[0].message == "unreadable page"
    assert not result.value.pages[2].failures


def test_pipeline_publishes_terminal_page_events() -> None:
    events: list[object] = []
    bus = InMemoryEventBus()
    bus.subscribe(events.append)

    result = PipelineOrchestrator(event_bus=bus).run(b"document")

    assert result.is_ok()
    assert events == [PipelineEvent("page_completed", 1)]
