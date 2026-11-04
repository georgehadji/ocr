"""Page identity, failures as data, and bounded streaming (audit PR1)."""

from __future__ import annotations

import io
import threading
import time
import xml.etree.ElementTree as ET
from typing import Iterator

import pytest
from PIL import Image

from omniocr.application.pipeline import InMemoryPage, NullRouter, PipelineOrchestrator
from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    OCRBlock,
    OCRLine,
    Suggestion,
    TenantContext,
    UnreadablePage,
    page_scoped_id,
)
from omniocr.domain.result import Err, Ok
from omniocr.infrastructure.exporters import AltoXmlExporter
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.review import build_review_document

CTX = TenantContext("org", "user", "desktop")


class _OneLinePerPage:
    """Numbers lines per page, as Tesseract and Kraken do: every page has ``line-1``."""

    def segment(self, page, context):
        return Ok((OCRLine("line-1", "", Confidence(0), BBox(0, 0, 50, 20)),))


class _Engine:
    name = "stub"

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def extract(self, page, context):
        if self._fail:
            return Err(EngineError("server down"))
        block = OCRBlock("b", f"text {page.number}", Confidence(90), BBox(0, 0, 50, 20))
        return Ok((block,))


class _SuggestEverything:
    def correct(self, line, context):
        return Ok((Suggestion(line.id, line.text, line.text.upper(), "test"),))


class _Pages:
    def __init__(self, count: int) -> None:
        self._count = count

    def stream(self, document: bytes) -> Iterator[InMemoryPage]:
        for number in range(1, self._count + 1):
            yield InMemoryPage(number, b"x", 60, 30)


def _pipeline(pages: int = 2, engine: _Engine | None = None, **kwargs) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        page_source=_Pages(pages),
        layout_analyzer=_OneLinePerPage(),
        router=NullRouter(engine or _Engine()),
        **kwargs,
    )


# --- [1] line ids are unique across the document --------------------------


def test_page_scoped_id_names_the_page() -> None:
    assert page_scoped_id(7, "line-3") == "p7-line-3"


def test_line_ids_are_unique_across_pages() -> None:
    document = _pipeline(pages=3).run(b"doc").value

    ids = [line.id for page in document.pages for line in page.lines]

    assert len(ids) == len(set(ids)) == 3
    assert ids[0].startswith("p1-") and ids[2].startswith("p3-")


def test_a_suggestion_on_page_two_stays_on_page_two() -> None:
    document = _pipeline(pages=2, post_corrector=_SuggestEverything()).run(b"doc").value

    review = build_review_document(document, [b"", b""])

    assert [len(page.lines[0].suggestions) for page in review.pages] == [1, 1]
    assert review.pages[1].lines[0].suggestions[0].source_text == "text 2"


def test_alto_export_has_no_duplicate_ids() -> None:
    document = _pipeline(pages=2).run(b"doc").value

    exported = AltoXmlExporter().export(document, CTX)

    assert exported.is_ok()
    ids = [
        elem.attrib["ID"] for elem in ET.fromstring(exported.value).iter() if "ID" in elem.attrib
    ]
    assert len(ids) == len(set(ids))


# --- [2] a page where every engine failed is a failure --------------------


def test_a_page_where_every_engine_failed_is_a_failure() -> None:
    document = _pipeline(pages=1, engine=_Engine(fail=True)).run(b"doc").value

    page = document.pages[0]
    assert [f.error_type for f in page.failures] == ["EngineError"]
    assert "server down" in page.failures[0].message
    # The layout boxes stay, so a reviewer still sees the page geometry.
    assert len(page.lines) == 1


def test_a_healthy_page_has_no_failure() -> None:
    assert not _pipeline(pages=1).run(b"doc").value.pages[0].failures


# --- [3] one unreadable page no longer ends the book ----------------------


def test_an_unreadable_page_is_a_failure_and_the_next_pages_are_read() -> None:
    class Source:
        def stream(self, document: bytes):
            yield InMemoryPage(1, b"x", 60, 30)
            yield UnreadablePage(2, "PDF page 2 could not be read: bad xref")
            yield InMemoryPage(3, b"x", 60, 30)

    pipeline = PipelineOrchestrator(
        page_source=Source(), layout_analyzer=_OneLinePerPage(), router=NullRouter(_Engine())
    )
    pages = pipeline.run(b"doc").value.pages

    assert [p.number for p in pages] == [1, 2, 3]
    assert pages[1].failures[0].error_type == "IngestError"
    assert "bad xref" in pages[1].failures[0].message
    assert not pages[0].failures and not pages[2].failures


def test_pdf_page_that_raises_does_not_end_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=72, height=72)
    payload = doc.tobytes()
    doc.close()

    real = DocumentPageSource._pdf_page

    def flaky(self, pdf, number, matrix):
        if number == 2:
            raise RuntimeError("corrupt content stream")
        return real(self, pdf, number, matrix)

    monkeypatch.setattr(DocumentPageSource, "_pdf_page", flaky)

    pages = list(DocumentPageSource().stream(payload))

    assert [p.number for p in pages] == [1, 2, 3]
    assert isinstance(pages[1], UnreadablePage)
    assert "corrupt content stream" in pages[1].error


# --- [19] multi-page TIFF -------------------------------------------------


def test_a_three_frame_tiff_yields_three_pages() -> None:
    frames = [Image.new("L", (20 + i, 10), 255 - 40 * i) for i in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])

    pages = list(DocumentPageSource().stream(buffer.getvalue()))

    assert [p.number for p in pages] == [1, 2, 3]
    assert [p.width for p in pages] == [20, 21, 22]
    assert all(p.content[:4] == b"\x89PNG" for p in pages)


# --- [13] one lazy path, bounded memory -----------------------------------


class _Tracking:
    """A source that counts how many pages are pulled but not yet yielded."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.produced = 0
        self.consumed = 0
        self.peak = 0

    def stream(self, document: bytes) -> Iterator[InMemoryPage]:
        for number in range(1, self.count + 1):
            self.produced += 1
            self.peak = max(self.peak, self.produced - self.consumed)
            yield InMemoryPage(number, b"x", 60, 30)


class _Slow:
    """Keeps pages in flight long enough for the window to fill."""

    def __init__(self) -> None:
        self.started = 0
        self._lock = threading.Lock()

    def process(self, page, context):
        with self._lock:
            self.started += 1
        time.sleep(0.005)
        return Ok(page)


def test_parallel_run_keeps_a_bounded_window_of_pages_in_flight() -> None:
    source = _Tracking(50)
    pipeline = PipelineOrchestrator(
        page_source=source,
        image_processor=_Slow(),
        layout_analyzer=_OneLinePerPage(),
        router=NullRouter(_Engine()),
        max_workers=4,
    )

    numbers = []
    for number, _ in pipeline.run_iteratively(b"doc"):
        source.consumed += 1
        numbers.append(number)

    assert numbers == list(range(1, 51))
    # The first page is yielded only once the window (2 * workers) is full.
    assert source.peak == 8


def test_stopping_early_abandons_the_queued_pages() -> None:
    slow = _Slow()
    pipeline = PipelineOrchestrator(
        page_source=_Tracking(50),
        image_processor=slow,
        layout_analyzer=_OneLinePerPage(),
        router=NullRouter(_Engine()),
        max_workers=2,
    )

    stream = pipeline.run_iteratively(b"doc")
    next(stream)
    next(stream)
    stream.close()

    assert slow.started <= 6


def test_sequential_run_isolates_any_page_error_like_the_parallel_one() -> None:
    class Boom:
        def process(self, page, context):
            if page.number == 2:
                raise RuntimeError("not a PipelineError")
            return Ok(page)

    for workers in (None, 3):
        pipeline = PipelineOrchestrator(
            page_source=_Pages(3),
            image_processor=Boom(),
            layout_analyzer=_OneLinePerPage(),
            router=NullRouter(_Engine()),
            max_workers=workers,
        )
        pages = pipeline.run(b"doc").value.pages
        assert [p.number for p in pages] == [1, 2, 3]
        assert [bool(p.failures) for p in pages] == [False, True, False]


def test_run_reports_a_source_failure_as_an_error() -> None:
    from omniocr.domain.errors import IngestError

    class Broken:
        def stream(self, document: bytes):
            raise IngestError("cannot open")
            yield  # pragma: no cover

    result = PipelineOrchestrator(page_source=Broken()).run(b"doc")

    assert isinstance(result, Err)
    assert "cannot open" in str(result.error)


def test_run_with_no_pages_is_an_error() -> None:
    class Empty:
        def stream(self, document: bytes):
            return iter(())

    result = PipelineOrchestrator(page_source=Empty()).run(b"doc")

    assert isinstance(result, Err)
