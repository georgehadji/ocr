from __future__ import annotations

import pytest

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.application.pipeline import (
    InMemoryPage,
    NullRouter,
    PipelineOrchestrator,
    build_document,
)
from omniocr.ports.lexicon import SetLexicon
from omniocr.domain.models import (
    BBox,
    Confidence,
    OCRBlock,
    OCRLine,
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


def test_suggest_only_corrector_expands_abbreviations_reversibly() -> None:
    """Common scholarly abbreviations like κ.τ.λ. get a reversible suggestion."""
    source = "ἄνθρωπος κ.τ.λ."
    line = OCRLine(
        id="line-abbr",
        text=source,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 50, 10),
        script=Script.ANCIENT,
    )

    disabled = SuggestOnlyCorrector(abbreviations={}).correct(
        line, TenantContext("org", "user", "desktop")
    )
    default = SuggestOnlyCorrector().correct(line, TenantContext("org", "user", "desktop"))

    assert disabled.is_ok()
    assert not any(s.reason == "reversible_abbreviation_expansion" for s in disabled.value)
    assert default.is_ok()
    assert any(s.suggestion_text == "ἄνθρωπος καὶ τὰ λοιπά" and s.reversible for s in default.value)
    assert line.text == source


def test_diacritic_validator_flags_multiple_breathing_marks() -> None:
    """NFD decomposition with two breathing marks on one base triggers a suggestion."""
    # Build a string with two combining breathing marks on alpha.
    alpha_with_two_breathings = (
        "\u03b1"  # alpha
        "\u0313"  # smooth breathing
        "\u0314"  # rough breathing — impossible combination
    )
    line = OCRLine(
        id="line-diacritic",
        text=alpha_with_two_breathings,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )
    result = SuggestOnlyCorrector().correct(line, TenantContext("org", "user", "desktop"))
    assert result.is_ok()
    reasons = {s.reason for s in result.value}
    assert "multiple_breathing_marks" in reasons


def test_diacritic_validator_flags_multiple_accent_marks() -> None:
    """NFD decomposition with two accent marks on one base triggers a suggestion."""
    alpha_with_two_accents = (
        "\u03b1"  # alpha
        "\u0300"  # grave
        "\u0301"  # acute — impossible combination
    )
    line = OCRLine(
        id="line-accents",
        text=alpha_with_two_accents,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )
    result = SuggestOnlyCorrector().correct(line, TenantContext("org", "user", "desktop"))
    assert result.is_ok()
    reasons = {s.reason for s in result.value}
    assert "multiple_accent_marks" in reasons


def test_diacritic_validator_passes_legitimate_polytonic_text() -> None:
    """Precomposed polytonic Greek with valid diacritics triggers no diacritic issue."""
    line = OCRLine(
        id="line-valid",
        text="ἄνθρωπος",  # precomposed: alpha with smooth + acute, valid
        confidence=Confidence(90),
        bbox=BBox(0, 0, 100, 10),
        script=Script.POLYTONIC,
    )
    result = SuggestOnlyCorrector().correct(line, TenantContext("org", "user", "desktop"))
    assert result.is_ok()
    reasons = {s.reason for s in result.value}
    assert "multiple_breathing_marks" not in reasons
    assert "multiple_accent_marks" not in reasons


def test_bundled_byzantine_lexicon_recognises_known_words() -> None:
    """Byzantine lexicon: known liturgical words pass, unknown words flagged."""
    from omniocr.infrastructure.lexicons import byzantine_lexicon

    lexicon = byzantine_lexicon()
    assert lexicon.contains("θεοτόκος")
    assert lexicon.contains("εὐαγγέλιον")
    assert lexicon.contains("λειτουργία")
    assert not lexicon.contains("ἄγνωστος")


def test_bundled_pontian_lexicon_recognises_known_words() -> None:
    """Pontian lexicon: known dialect words pass, standard Greek words flagged."""
    from omniocr.infrastructure.lexicons import pontian_lexicon

    lexicon = pontian_lexicon()
    assert lexicon.contains("εμάν")
    assert lexicon.contains("τραγωδώ")
    assert lexicon.contains("ψωμίν")
    assert not lexicon.contains("ἄνθρωπος")


def test_lexicons_by_script_returns_mapping() -> None:
    """lexicons_by_script() returns a non-empty mapping of Script→lexicon."""
    from omniocr.infrastructure.lexicons import lexicons_by_script

    mapping = lexicons_by_script()
    assert Script.BYZANTINE in mapping
    assert Script.PONTIAN in mapping
    assert mapping[Script.BYZANTINE].name == "byzantine"
    assert mapping[Script.PONTIAN].name == "pontian"


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


def test_redis_job_store_round_trips_document() -> None:
    """RedisJobStore persists and reloads a checkpoint.

    Uses a stub ``redis`` client in place of a live server so the JSON
    serialization/deserialization round-trip is verified deterministically.
    Skipped automatically if the ``redis`` package is unavailable, per
    the implementation plan's ``pytest.importorskip`` requirement.
    """
    pytest.importorskip("redis")

    from omniocr.infrastructure.jobs import RedisJobStore

    class StubRedis:
        """Minimal in-memory stand-in mimicking ``redis`` set/get semantics."""

        def __init__(self) -> None:
            self._data: dict[str, str] = {}

        def set(self, key: str, value: str) -> None:
            self._data[key] = value

        def get(self, key: str) -> bytes | None:
            raw = self._data.get(key)
            return raw.encode("utf-8") if raw is not None else None

    store = RedisJobStore()
    store._redis = StubRedis()  # substitute a stub client so no live server is needed

    original = build_document(
        (
            OCRLine(
                id="l1", text="ἄνθρωπος", confidence=Confidence(92.0), bbox=BBox(0, 0, 100, 20)
            ),
            OCRLine(id="l2", text="λόγος", confidence=Confidence(88.0), bbox=BBox(0, 20, 80, 20)),
        ),
        page_number=1,
        width=500,
        height=700,
    )

    result = store.checkpoint("job-redis", original)
    loaded = store.load("job-redis")

    assert result.is_ok()
    assert loaded is not None
    assert loaded == original
    assert loaded.pages[0].lines[0].text == "ἄνθρωπος"  # polytonic survives round-trip


def test_redis_job_store_ping_reports_unreachable_server() -> None:
    """RedisJobStore.ping() returns False when no Redis server is reachable."""
    pytest.importorskip("redis")

    from omniocr.infrastructure.jobs import RedisJobStore

    # The default localhost:6379 has no server in the test environment, so
    # ping() must report unreachable (False) rather than raise.
    store = RedisJobStore("redis://localhost:6379/0")
    assert store.ping() is False


def test_settings_loads_redis_url_from_env() -> None:
    """Settings reads OMNIOCR_REDIS_URL, defaulting to localhost."""
    from omniocr.infrastructure.config import Settings

    default = Settings.from_env({})
    assert default.redis_url == "redis://localhost:6379/0"

    custom = Settings.from_env({"OMNIOCR_REDIS_URL": "redis://my-broker:7000/2"})
    assert custom.redis_url == "redis://my-broker:7000/2"


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
    assert len(events) == 1
    assert events[0].event_type == "page_completed"
    assert events[0].page_number == 1
    assert events[0].duration_ms >= 0


def test_pipeline_extracts_each_engine_once_and_assigns_blocks_to_segments() -> None:
    class TwoLineLayout:
        def segment(self, page, context):
            return Ok(
                (
                    OCRLine("segment-1", "", Confidence(0), BBox(0, 0, 50, 20)),
                    OCRLine("segment-2", "", Confidence(0), BBox(0, 30, 50, 20)),
                )
            )

    class PageEngine:
        name = "page-engine"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, page, context):
            self.calls += 1
            return Ok(
                (
                    OCRBlock("top", "top", Confidence(90), BBox(2, 2, 20, 10)),
                    OCRBlock("bottom", "bottom", Confidence(80), BBox(2, 32, 20, 10)),
                )
            )

    engine = PageEngine()
    result = PipelineOrchestrator(
        layout_analyzer=TwoLineLayout(),
        router=NullRouter(engine),
    ).run(b"page")

    assert result.is_ok()
    assert engine.calls == 1
    assert [line.text for line in result.value.pages[0].lines] == ["top", "bottom"]


def test_parallel_pipeline_processes_pages_concurrently() -> None:
    """PipelineOrchestrator with max_workers processes pages in parallel.

    Verifies that all pages complete, page order is preserved, and
    per-page failure isolation works under parallelism.
    """
    import threading
    import time

    seen_threads: set[int] = set()

    class ThreadTrackingEngine:
        name = "thread-tracker"
        calls = 0

        def extract(self, page, context):
            self.calls += 1
            seen_threads.add(threading.get_ident())
            # Small sleep so the thread pool distributes work across workers.
            time.sleep(0.01)
            return Ok(
                (
                    OCRBlock(
                        f"b-{page.number}",
                        f"page-{page.number}",
                        Confidence(90),
                        BBox(0, 0, 10, 10),
                    ),
                )
            )

    engine = ThreadTrackingEngine()
    # Create 5 pages to process in parallel.
    raw_pages = [
        InMemoryPage(number=i, content=f"page-{i}".encode(), width=10, height=10)
        for i in range(1, 6)
    ]

    class FixedPageSource:
        def stream(self, document):
            return iter(raw_pages)

    pipeline = PipelineOrchestrator(
        page_source=FixedPageSource(),
        router=NullRouter(engine),
        max_workers=3,
    )
    result = pipeline.run(b"dummy")

    assert result.is_ok()
    doc = result.value
    assert len(doc.pages) == 5
    # Page order must be preserved.
    assert [p.number for p in doc.pages] == [1, 2, 3, 4, 5]
    assert [p.lines[0].text for p in doc.pages] == [
        "page-1",
        "page-2",
        "page-3",
        "page-4",
        "page-5",
    ]
    assert engine.calls == 5
    # With max_workers=3 and 5 pages, at least 2 threads should be used.
    assert len(seen_threads) >= 2, f"Expected ≥2 threads, got {len(seen_threads)}"


def test_parallel_pipeline_preserves_failure_isolation() -> None:
    """Failed pages under parallelism produce PageFailure, not pipeline abort."""

    class FailOnPage3Engine:
        name = "selective-fail"

        def extract(self, page, context):
            if page.number == 3:
                raise RuntimeError("simulated engine crash")
            return Ok(
                (
                    OCRBlock(
                        f"ok-{page.number}",
                        f"text-{page.number}",
                        Confidence(90),
                        BBox(0, 0, 10, 10),
                    ),
                )
            )

    engine = FailOnPage3Engine()
    raw_pages = [InMemoryPage(number=i, content=b"x", width=10, height=10) for i in range(1, 6)]

    class FixedPageSource:
        def stream(self, document):
            return iter(raw_pages)

    pipeline = PipelineOrchestrator(
        page_source=FixedPageSource(),
        router=NullRouter(engine),
        max_workers=2,
    )
    result = pipeline.run(b"dummy")

    assert result.is_ok()
    doc = result.value
    assert len(doc.pages) == 5
    # Page 3 should have a failure.
    page3 = doc.pages[2]  # 0-indexed
    assert page3.number == 3
    assert len(page3.failures) == 1
    assert "simulated engine crash" in page3.failures[0].message
    # Other pages should have text.
    assert doc.pages[0].lines[0].text == "text-1"


def test_count_pages_uses_fitz_length_for_pdf() -> None:
    """count_pages() uses PyMuPDF's fast page count for valid PDF bytes.

    The fallback streaming path should only be exercised when PyMuPDF
    cannot determine a count (e.g. non-PDF bytes or ImportError).
    """
    try:
        import fitz
    except ImportError:
        raise pytest.skip("PyMuPDF not installed")

    # Build a minimal 3-page PDF in memory.
    document = fitz.open()
    for _ in range(3):
        document.new_page(width=200, height=200)
    pdf_bytes = document.tobytes()
    document.close()

    pipeline = PipelineOrchestrator()
    assert pipeline.count_pages(pdf_bytes) == 3


def test_count_pages_falls_back_to_streaming_for_non_pdf() -> None:
    """count_pages() falls back to the page source stream for non-PDF bytes."""

    class ThreePageSource:
        def stream(self, document):
            yield InMemoryPage(number=1, content=b"")
            yield InMemoryPage(number=2, content=b"")
            yield InMemoryPage(number=3, content=b"")

    pipeline = PipelineOrchestrator(page_source=ThreePageSource())
    # Non-PDF bytes: fitz.open raises TypeError, triggering the fallback.
    assert pipeline.count_pages(b"not a pdf at all") == 3
