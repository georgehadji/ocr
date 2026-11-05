from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Iterator, Protocol, Sequence, cast
from omniocr.application.alignment import agreement_tier
from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.domain.errors import EngineError, ExportError, IngestError, LayoutError, PipelineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    OCRBlock,
    PageFailure,
    PipelineEvent,
    Script,
    Suggestion,
    TenantContext,
    UnreadablePage,
    page_scoped_id,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import (
    IDocumentAssembler,
    IEventBus,
    IExporter,
    IImageProcessor,
    ILayoutAnalyzer,
    IOCREngine,
    IPageSource,
    IPostCorrector,
    IReconciler,
    IRouter,
    RawPage,
)
from omniocr.application.structure import IdentityAssembler

__all__ = [
    "FirstCandidateReconciler",
    "InMemoryPage",
    "NullPageSource",
    "NullRouter",
    "PassthroughImageProcessor",
    "PipelineOrchestrator",
    "SingleLineLayoutAnalyzer",
    # Re-exported for composition roots and edition UIs.
    "SuggestOnlyCorrector",
    "build_document",
]


def _get_logger(name: str) -> _StructuredLogger:
    """Return a structlog logger if available, else a stdlib-backed adapter.

    ``structlog`` is a dev-only extra, not a core dependency, so a base
    install must not crash when structured keyword fields (``page=``,
    ``duration_ms=``) are passed to log calls.
    """
    try:
        import structlog

        return cast("_StructuredLogger", structlog.get_logger(name))
    except ImportError:
        return _StdlibLoggerAdapter(logging.getLogger(name))


class _StructuredLogger(Protocol):
    def info(self, event: str, **kwargs: object) -> None: ...
    def warning(self, event: str, **kwargs: object) -> None: ...
    def error(self, event: str, **kwargs: object) -> None: ...


class _StdlibLoggerAdapter:
    """Adapts stdlib ``logging.Logger`` to accept structlog-style keyword fields."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, event: str, **kwargs: object) -> None:
        self._logger.info(event, extra=kwargs)

    def error(self, event: str, **kwargs: object) -> None:
        self._logger.error(event, extra=kwargs)

    def warning(self, event: str, **kwargs: object) -> None:
        self._logger.warning(event, extra=kwargs)


@dataclass(frozen=True, slots=True)
class InMemoryPage:
    number: int
    content: bytes
    width: int = 1
    height: int = 1


class NullPageSource:
    def stream(self, document: bytes) -> Iterator[RawPage]:
        payload = document if document else b""
        yield InMemoryPage(number=1, content=payload, width=1, height=1)


class PassthroughImageProcessor:
    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Ok(page)


class SingleLineLayoutAnalyzer:
    def __init__(self, script: Script = Script.UNKNOWN) -> None:
        self.script = script

    def segment(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRLine], LayoutError]:
        line = OCRLine(
            id=f"line-{page.number}",
            text="",
            confidence=Confidence(0.0),
            bbox=BBox(0, 0, max(1, page.width), max(1, page.height)),
            script=self.script,
        )
        return Ok((line,))


class NullRouter:
    def __init__(self, engine: IOCREngine | None = None) -> None:
        self._engine = engine

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        return (self._engine,) if self._engine is not None else ()


class _DefaultTextExporter:
    """Minimal ``IExporter`` default: one line per recognized segment.

    Kept local (not imported from ``infrastructure``) so ``application``
    does not depend on a concrete adapter package. Real editions inject a
    richer exporter (Markdown/DOCX/PDF/ALTO) via composition.
    """

    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        lines = [line.text for page in document.pages for line in page.lines]
        return Ok("\n".join(lines).encode("utf-8"))


class FirstCandidateReconciler:
    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]:
        if not candidates:
            return Err(EngineError("no OCR candidates available"))
        return Ok(candidates[0])


def _is_primary(line: OCRLine) -> bool:
    """True when this candidate came from the primary (unvaried) page.

    A candidate with no provenance is treated as primary: the layout segment
    fallback has none, and it must stay selectable or a page with no engine
    output would lose its only line.
    """
    provenance = line.provenance
    return provenance is None or not getattr(provenance, "variant", "")


def build_document(
    lines: Sequence[OCRLine], page_number: int = 1, width: int = 1, height: int = 1
) -> DocumentStructure:
    page = DocumentPage(number=page_number, width=width, height=height, lines=tuple(lines))
    return DocumentStructure(pages=(page,))


def _or_default(context: TenantContext | None) -> TenantContext:
    return context or TenantContext(
        organization_id="default", user_id="system", subscription_tier="desktop"
    )


class PipelineOrchestrator:
    def __init__(
        self,
        page_source: IPageSource | None = None,
        image_processor: IImageProcessor | None = None,
        layout_analyzer: ILayoutAnalyzer | None = None,
        router: IRouter | None = None,
        reconciler: IReconciler | None = None,
        post_corrector: IPostCorrector | None = None,
        exporter: IExporter | None = None,
        assembler: IDocumentAssembler | None = None,
        event_bus: IEventBus | None = None,
        max_workers: int | None = None,
        variants: Sequence[tuple[str, IImageProcessor]] = (),
    ) -> None:
        # Extra preprocessing variants (ENHANCEMENT_PLAN A3). Empty by default,
        # so this changes nothing for a caller that does not ask: N variants
        # cost N times the recognition, which is the dominant cost of a run.
        self._variants = tuple(variants)
        self._page_source = page_source or NullPageSource()
        self._image_processor = image_processor or PassthroughImageProcessor()
        self._layout_analyzer = layout_analyzer or SingleLineLayoutAnalyzer()
        self._router = router or NullRouter()
        self._reconciler = reconciler or FirstCandidateReconciler()
        self._post_corrector = post_corrector or SuggestOnlyCorrector()
        self._exporter = exporter or _DefaultTextExporter()
        self._assembler = assembler or IdentityAssembler()
        self._event_bus = event_bus
        self._max_workers = max_workers
        self._log = _get_logger("omniocr.pipeline")

    def run(
        self, document: bytes, context: TenantContext | None = None
    ) -> Result[DocumentStructure, PipelineError]:
        """Recognize every page and assemble the document.

        A fold over the same lazy page path ``run_iteratively`` uses, so a
        single-shot run and a streamed run apply one per-page error rule and
        hold at most a bounded window of pages in memory.
        """
        ctx = _or_default(context)
        self._log.info("pipeline_start")
        try:
            pages = tuple(self._pages(document, ctx))
        except PipelineError as exc:
            return Err(exc)
        if not pages:
            return Err(PipelineError("no pages were produced from the input"))
        return self.assemble(DocumentStructure(pages=pages), ctx)

    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, PipelineError]:
        """Apply the injected assembler (paragraphs, hyphen joins, running heads).

        ``run()`` does this itself; a ``run_iteratively()`` caller must call it
        on the collected pages before ``export()``, or ``--structure`` is lost.
        """
        result = self._assembler.assemble(document, context)
        if isinstance(result, Err):
            return Err(result.error)
        return result

    def count_pages(self, document: bytes) -> int:
        """Return the number of pages in a document without processing them."""
        # PyMuPDF: fast page count from PDF header without streaming.
        try:
            import fitz

            source = fitz.open(stream=document, filetype="pdf")
            count = len(source)
            source.close()
            return max(count, 1)
        except (ImportError, TypeError, RuntimeError):
            pass
        count = sum(1 for _ in self._page_source.stream(document))
        return max(count, 1)

    def run_iteratively(
        self,
        document: bytes,
        context: TenantContext | None = None,
    ) -> Iterator[tuple[int, DocumentPage]]:
        """Process pages one at a time, yielding ``(page_number, DocumentPage)``.

        Unlike ``run()``, this does NOT return a ``DocumentStructure`` or
        ``Result`` — it yields pages as they finish so the caller can
        report progress in real time. Per-page failures are wrapped in
        ``DocumentPage`` with a ``PageFailure`` entry.

        Pages come out in page order. With ``max_workers > 1`` up to
        ``2 * max_workers`` pages are in flight at once, so memory stays flat
        on a long book and stopping early (``--max-pages``) wastes at most
        that window.

        Error behaviour: If the page source itself raises a
        ``PipelineError`` (e.g. corrupt document, I/O failure), the
        generator exits silently — no page is yielded for the failing
        stream position. Callers that need an explicit error signal
        should use ``run()`` instead, which returns a ``Result``.
        """
        try:
            for page in self._pages(document, _or_default(context)):
                yield (page.number, page)
        except PipelineError as exc:
            self._log.error("pipeline_failed", error=str(exc))

    def _pages(self, document: bytes, ctx: TenantContext) -> Iterator[DocumentPage]:
        """The one page path: lazy, in page order, with a bounded window in flight.

        The source is consumed in the calling thread (PyMuPDF is not
        thread-safe); only recognition fans out. Events are published here,
        in page order, so event-bus adapters never see two threads at once.
        """
        raw_pages = self._page_source.stream(document)
        workers = self._max_workers or 1
        if workers <= 1:
            for raw_page in raw_pages:
                yield self._announce(*self._guarded(raw_page, ctx))
            return
        window: deque[Future[tuple[DocumentPage, float]]] = deque()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            try:
                for raw_page in raw_pages:
                    window.append(executor.submit(self._guarded, raw_page, ctx))
                    if len(window) >= 2 * workers:
                        yield self._announce(*window.popleft().result())
                while window:
                    yield self._announce(*window.popleft().result())
            finally:
                # A consumer that stops early abandons the queued pages.
                for pending in window:
                    pending.cancel()

    def _guarded(self, raw_page: RawPage, ctx: TenantContext) -> tuple[DocumentPage, float]:
        """Process one page; any error becomes a failure on that page, not the run's."""
        started_at = perf_counter()
        try:
            page = self._process_page(raw_page, ctx)
        except Exception as exc:
            page = DocumentPage(
                number=raw_page.number,
                width=getattr(raw_page, "width", 1),
                height=getattr(raw_page, "height", 1),
                failures=(PageFailure(error_type=type(exc).__name__, message=str(exc)),),
            )
        return page, (perf_counter() - started_at) * 1000

    def _announce(self, page: DocumentPage, duration_ms: float) -> DocumentPage:
        if page.failures:
            message = "; ".join(failure.message for failure in page.failures)
            if self._event_bus is not None:
                self._event_bus.publish(
                    PipelineEvent("page_failed", page.number, message, duration_ms)
                )
            self._log.warning("page_failed", page=page.number, error=message)
        else:
            if self._event_bus is not None:
                self._event_bus.publish(
                    PipelineEvent("page_completed", page.number, duration_ms=duration_ms)
                )
            self._log.info("page_completed", page=page.number, duration_ms=round(duration_ms, 1))
        return page

    def _variant_pages(
        self, raw_page: RawPage, context: TenantContext
    ) -> list[tuple[str, RawPage]]:
        """Preprocess the page once per variant (ENHANCEMENT_PLAN A3).

        The same model on the same line, given differently preprocessed images,
        makes *different* errors — so N variants feed A4's merge and produce
        ensemble benefit from a single model. The primary comes first and is
        the page layout is segmented from.

        A failing variant is dropped with a warning rather than failing the
        page: losing one binarization costs accuracy, losing the page costs
        everything. The primary is exempt — if that fails there is no layout to
        segment and nothing to fall back to.

        Every variant must preserve geometry, and one that does not is dropped.
        Word boxes from all variants are matched against segments found once on
        the primary, so a variant that rotated or rescaled pixels would
        silently misassign every block. That rules out deskew and upscaling as
        variants whatever A3's illustrative list says — deskew belongs in the
        always-on chain ahead of the fan-out, where it moves the page and its
        layout together.
        """
        primary = self._image_processor.process(raw_page, context)
        if isinstance(primary, Err):
            raise primary.error
        assert isinstance(primary, Ok)
        pages: list[tuple[str, RawPage]] = [("primary", primary.value)]

        for name, processor in self._variants:
            produced = processor.process(raw_page, context)
            if isinstance(produced, Err):
                self._log.warning(
                    "variant_failed",
                    page=raw_page.number,
                    variant=name,
                    error=str(produced.error),
                )
                continue
            assert isinstance(produced, Ok)
            if (
                produced.value.width != primary.value.width
                or produced.value.height != primary.value.height
            ):
                self._log.warning(
                    "variant_changed_geometry",
                    page=raw_page.number,
                    variant=name,
                    detail="dropped: its word boxes would not match the primary layout",
                )
                continue
            pages.append((name, produced.value))
        return pages

    def _page_from_text_layer(
        self, raw_page: RawPage, lines: Sequence[OCRLine], context: TenantContext
    ) -> DocumentPage:
        """Build a page from the PDF's own text instead of recognizing pixels.

        Preprocessing, layout segmentation, routing, and every engine are
        skipped. The PDF already states its own line structure; rendering it to
        a bitmap and segmenting that to rediscover the same lines is strictly
        worse, and recognizing characters the file already contains exactly
        replaces a CER of 0.000 with the measured 0.1226.

        Nothing is reconciled either. There is one reading, so a vote would
        need a second engine run — burning the whole time saving — and would
        then let self-reported confidence, the one signal this codebase has
        repeatedly caught lying, choose recognition over exact text.

        Post-correction still runs. A born-digital PDF can carry a typo, the
        checks are suggest-only either way, and skipping them would quietly
        give these pages a weaker review than recognized ones.
        """
        suggestions: list[Suggestion] = []
        for line in lines:
            corrections = self._post_corrector.correct(line, context)
            if isinstance(corrections, Err):
                raise corrections.error
            assert isinstance(corrections, Ok)
            suggestions.extend(corrections.value)
        return DocumentPage(
            number=raw_page.number,
            width=getattr(raw_page, "width", 1),
            height=getattr(raw_page, "height", 1),
            lines=tuple(lines),
            suggestions=tuple(suggestions),
        )

    def _process_page(self, raw_page: RawPage, context: TenantContext) -> DocumentPage:
        # Read off the *raw* page, before `_variant_pages`: preprocessing
        # returns new page objects that do not carry the field. Accessed
        # defensively because `RawPage` is a structural protocol that only the
        # PDF source can satisfy this far — the same pattern as
        # `provenance.variant` and `lexicon.layers`.
        if isinstance(raw_page, UnreadablePage):
            return DocumentPage(
                number=raw_page.number,
                width=raw_page.width,
                height=raw_page.height,
                failures=(PageFailure(error_type="IngestError", message=raw_page.error),),
            )
        text_lines: Sequence[OCRLine] = getattr(raw_page, "text_lines", ())
        if text_lines:
            return self._page_from_text_layer(raw_page, text_lines, context)

        variant_pages = self._variant_pages(raw_page, context)
        processed_page = variant_pages[0][1]

        segments = self._layout_analyzer.segment(processed_page, context)
        if isinstance(segments, Err):
            raise segments.error
        assert isinstance(segments, Ok)
        # Engines number lines per page (``line-3``); the page number makes the
        # id unique across the document, here and nowhere else.
        segment_values = tuple(
            replace(segment, id=page_scoped_id(raw_page.number, segment.id))
            for segment in segments.value
        )

        failed_engines: list[str] = []
        page_lines: list[OCRLine] = []
        suggestions: list[Suggestion] = []
        # Keyed by (engine identity, variant name): one engine reading two
        # variants is two independent results, and collapsing them would throw
        # away exactly the disagreement the ensemble exists to exploit.
        engine_results: dict[tuple[int, str], Sequence[OCRBlock]] = {}
        engine_assignments: dict[tuple[int, str], dict[int, list[OCRBlock]]] = {}
        for segment_index, segment in enumerate(segment_values):
            engines = self._router.route(segment, context)
            candidate_lines: list[OCRLine] = []
            for variant_name, variant_page in variant_pages:
                for engine in engines:
                    engine_key = (id(engine), variant_name)
                    if engine_key not in engine_results:
                        extracted = engine.extract(variant_page, context)
                        if isinstance(extracted, Err):
                            # An engine failing is survivable — the others
                            # still vote — but it must never be silent. An
                            # ensemble that quietly degrades to one engine
                            # looks identical to a healthy one from outside.
                            engine_results[engine_key] = ()
                            engine_name = getattr(engine, "name", type(engine).__name__)
                            failed_engines.append(f"{engine_name}: {extracted.error}")
                            self._log.warning(
                                "engine_failed",
                                page=raw_page.number,
                                engine=engine_name,
                                variant=variant_name,
                                error=str(extracted.error),
                            )
                        else:
                            assert isinstance(extracted, Ok)
                            engine_results[engine_key] = extracted.value
                        engine_assignments[engine_key] = self._assign_blocks(
                            segment_values, engine_results[engine_key]
                        )
                    # All of an engine's blocks inside this segment belong to
                    # the SAME line, so they compose into one candidate.
                    # Emitting one candidate per block would make a line's own
                    # words compete against each other and the reconciler
                    # would keep exactly one — silently discarding the rest.
                    overlapping = tuple(engine_assignments[engine_key].get(segment_index, ()))
                    if overlapping:
                        candidate_lines.append(
                            self._compose_line(segment, engine, overlapping, variant_name)
                        )
            if not candidate_lines:
                candidate_lines.append(segment)
            # Variants vote; they do not compete.
            #
            # A variant is the same engine on transformed input, so letting one
            # *win selection* means trusting self-reported confidence to say
            # which binarization the engine read better — and confidence is the
            # one signal this codebase has repeatedly caught lying. Measured
            # 2026-09-05, allowing variants into selection made the chosen line
            # monotonically worse as variants were added: CER 0.1226 (1 variant)
            # -> 0.1283 (2) -> 0.1306 (3), before any merge ran.
            #
            # So selection sees only the primary, and every variant still
            # reaches the merge below, where agreement across independent
            # readings is real evidence rather than an engine's opinion of
            # itself. This also makes the fan-out monotone: adding variants can
            # no longer make the result worse than not adding them.
            selectable = [line for line in candidate_lines if _is_primary(line)] or candidate_lines
            chosen = self._reconciler.reconcile(selectable, context)
            if isinstance(chosen, Err):
                raise chosen.error
            assert isinstance(chosen, Ok)
            chosen_line = chosen.value
            # A5: how much the engines agreed, recorded on the line. This is a
            # better confidence signal than any engine's self-report, and it
            # orders the review queue. It never gates export - a SPLIT line
            # exports its text, flagged, because dropping low-agreement lines
            # would be a faithfulness violation dressed as quality control.
            chosen_line = replace(chosen_line, agreement=agreement_tier(candidate_lines))
            # A reconciler that can also merge (A4's AlignedReconciler) offers
            # the voted line here. It is a suggestion, never the line itself:
            # a merge is text no single engine produced, so CLAUDE.md rule 1
            # keeps it out of `OCRLine.text` and in front of a human instead.
            suggester = getattr(self._reconciler, "suggest", None)
            if suggester is not None:
                merged = suggester(chosen_line, candidate_lines)
                if merged is not None:
                    suggestions.append(merged)
            corrections = self._post_corrector.correct(chosen_line, context)
            if isinstance(corrections, Err):
                raise corrections.error
            assert isinstance(corrections, Ok)
            page_lines.append(chosen_line)
            suggestions.extend(corrections.value)

        # A page that produced no lines is reported as a failure, not as a
        # successful empty page. Segmenters can return zero lines without
        # raising, and that used to surface as `page N: ok (0 lines)` — a job
        # could drop a third of a book and still look clean to a scripted
        # caller. A blank leaf looks the same, so the CLI lists these pages
        # apart (`empty_pages`) and fails the run only when every page was
        # empty; the exports and the stderr summary keep each one visible.
        failures: list[PageFailure] = []
        if not page_lines:
            failures.append(
                PageFailure(
                    error_type="EmptyPage",
                    message="no text lines were produced for this page",
                )
            )
        # Every engine that ran on this page failed: the lines are the layout
        # boxes with no text. Kept so a reviewer sees the geometry, but the
        # page must not read as a clean one.
        if engine_results and len(failed_engines) == len(engine_results):
            failures.append(
                PageFailure(
                    error_type="EngineError",
                    message="every engine failed on this page: " + "; ".join(failed_engines),
                )
            )

        return DocumentPage(
            number=raw_page.number,
            width=getattr(raw_page, "width", 1),
            height=getattr(raw_page, "height", 1),
            lines=tuple(page_lines),
            suggestions=tuple(suggestions),
            failures=tuple(failures),
        )

    @staticmethod
    def _compose_line(
        segment: OCRLine,
        engine: IOCREngine,
        blocks: Sequence[OCRBlock],
        variant: str = "",
    ) -> OCRLine:
        """Compose one engine's blocks within a segment into a single candidate line.

        Block order is the engine's own emission order — Tesseract and Kraken
        both emit in reading order. Re-sorting here would substitute a layout
        heuristic for the engine's own judgement, which is an orthographic
        decision this layer is not permitted to make.

        Confidence is the mean over contributing blocks, so a line is only as
        trustworthy as its words. Every block is retained on the line so the
        review UI can show per-engine disagreement and exporters can emit
        word-level boxes for the searchable-PDF text layer.
        """
        texts = [block.text for block in blocks if block.text]
        confidences = [block.confidence.value for block in blocks]
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        # The variant is stamped onto both the id and the provenance. Without
        # it, one engine reading three variants would emit three candidates
        # sharing an id and an indistinguishable provenance — A4's merge would
        # treat them as one voter and a reviewer could not tell which image a
        # reading came from. ENHANCEMENT_PLAN A3 calls this out as the point
        # where faithfulness breaks if the field is missing.
        provenance = blocks[0].provenance if blocks else None
        if provenance is not None and variant and variant != "primary":
            provenance = replace(provenance, variant=variant)
        suffix = "" if not variant or variant == "primary" else f"-{variant}"
        return OCRLine(
            id=f"{segment.id}-{engine.name}{suffix}",
            text=" ".join(texts),
            confidence=Confidence(mean_confidence),
            bbox=PipelineOrchestrator._union_bbox(blocks) or segment.bbox,
            script=segment.script,
            region_type=segment.region_type,
            reading_order=segment.reading_order,
            blocks=tuple(blocks),
            provenance=provenance,
        )

    @staticmethod
    def _union_bbox(blocks: Sequence[OCRBlock]) -> BBox | None:
        """Smallest box enclosing every block, or None when there are none."""
        if not blocks:
            return None
        left = min(block.bbox.x for block in blocks)
        top = min(block.bbox.y for block in blocks)
        right = max(block.bbox.right for block in blocks)
        bottom = max(block.bbox.bottom for block in blocks)
        return BBox(x=left, y=top, w=max(1, right - left), h=max(1, bottom - top))

    @staticmethod
    def _boxes_overlap(first: BBox, second: BBox) -> bool:
        return (
            first.x < second.right
            and second.x < first.right
            and first.y < second.bottom
            and second.y < first.bottom
        )

    @staticmethod
    def _overlap_area(first: BBox, second: BBox) -> int:
        width = min(first.right, second.right) - max(first.x, second.x)
        height = min(first.bottom, second.bottom) - max(first.y, second.y)
        return width * height if width > 0 and height > 0 else 0

    @classmethod
    def _assign_blocks(
        cls, segments: Sequence[OCRLine], blocks: Sequence[OCRBlock]
    ) -> dict[int, list[OCRBlock]]:
        """Assign every block to the one line it overlaps most.

        A word belongs to exactly one line. Selecting by *any* non-zero
        overlap did not enforce that: printed text lines are stacked boxes
        whose bounds routinely graze their neighbours over ascenders and
        descenders, so a word touching the next line by a pixel joined it too.

        Measured on page 31 of the target document: **385 of 513 words (75%)
        were claimed by more than one line** — 330 by two, 54 by three, one by
        four. Each line therefore emitted its own words plus a slice of the
        lines above and below, which is exactly what the first full run
        produced: overlapping, lossy repetitions of the same sentence.

        Ties keep the earliest segment, so the choice stays in reading order
        and is deterministic. A block that overlaps nothing is dropped here —
        the segment then has no candidate and falls back to its own text.
        """
        assignment: dict[int, list[OCRBlock]] = {}
        for block in blocks:
            best_index = -1
            best_area = 0
            for index, segment in enumerate(segments):
                area = cls._overlap_area(segment.bbox, block.bbox)
                if area > best_area:
                    best_index, best_area = index, area
            if best_index >= 0:
                assignment.setdefault(best_index, []).append(block)
        return assignment

    def export(
        self, document: DocumentStructure, context: TenantContext | None = None
    ) -> Result[bytes, PipelineError]:
        ctx = context or TenantContext(
            organization_id="default", user_id="system", subscription_tier="desktop"
        )
        result = self._exporter.export(document, ctx)
        if isinstance(result, Err):
            return Err(result.error)
        assert isinstance(result, Ok)
        return Ok(result.value)
