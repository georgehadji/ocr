from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Iterable, Iterator, Protocol, Sequence, cast
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
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import (
    IDocumentAssembler,
    IEventBus,
    IExporter,
    IImageProcessor,
    IJobStore,
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
        job_store: IJobStore | None = None,
        event_bus: IEventBus | None = None,
        max_workers: int | None = None,
        checkpoint_every: int = 25,
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
        self._job_store = job_store
        self._event_bus = event_bus
        self._max_workers = max_workers
        if checkpoint_every < 1:
            raise ValueError("checkpoint_every must be >= 1")
        self._checkpoint_every = checkpoint_every
        self._log = _get_logger("omniocr.pipeline")

    def run(
        self,
        document: bytes,
        context: TenantContext | None = None,
        job_id: str | None = None,
        resume_job_id: str | None = None,
    ) -> Result[DocumentStructure, PipelineError]:
        ctx = context or TenantContext(
            organization_id="default", user_id="system", subscription_tier="desktop"
        )
        checkpoint_id = job_id or resume_job_id
        loaded_checkpoint = None
        if self._job_store is not None and checkpoint_id is not None:
            loaded_checkpoint = self._job_store.load(checkpoint_id)
        pages: list[DocumentPage] = (
            list(loaded_checkpoint.pages) if loaded_checkpoint is not None else []
        )
        completed_numbers = {page.number for page in pages}
        self._log.info("pipeline_start", page_count=len(pages), resume=resume_job_id is not None)

        try:
            page_stream = self._page_source.stream(document)
            raw_pages = list(page_stream)
        except PipelineError as exc:
            return Err(exc)

        # Filter out already-completed pages (from checkpoint resume).
        pending = [rp for rp in raw_pages if rp.number not in completed_numbers]

        if self._max_workers is not None and self._max_workers > 1 and len(pending) > 1:
            pages = self._run_parallel(pending, ctx, pages, checkpoint_id)
        else:
            pages = self._run_sequential(pending, ctx, pages, checkpoint_id)

        doc = DocumentStructure(pages=tuple(pages))
        return self._assembler.assemble(doc, ctx)

    def _run_parallel(
        self,
        raw_pages: list[RawPage],
        ctx: TenantContext,
        pages: list[DocumentPage],
        checkpoint_id: str | None,
    ) -> list[DocumentPage]:
        """Process pages concurrently with a ThreadPoolExecutor.

        Results are collected in page-number order. Per-page failures are
        isolated — a failed page produces a ``DocumentPage`` with a
        ``PageFailure`` entry and does not abort the whole run.
        Event-bus and job-store operations are serialized in order after
        all pages finish to avoid threading issues in those adapters.
        """
        page_results: dict[int, DocumentPage] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_page: dict[Future[DocumentPage], int] = {}
            for raw_page in raw_pages:
                future = executor.submit(self._process_page, raw_page, ctx)
                future_to_page[future] = raw_page.number

            for future in as_completed(future_to_page):
                page_number = future_to_page[future]
                try:
                    page_results[page_number] = future.result()
                except Exception as exc:
                    # Map to the raw page that failed.
                    raw = next(rp for rp in raw_pages if rp.number == page_number)
                    page_results[page_number] = DocumentPage(
                        number=page_number,
                        width=getattr(raw, "width", 1),
                        height=getattr(raw, "height", 1),
                        failures=(PageFailure(error_type=type(exc).__name__, message=str(exc)),),
                    )
                    if self._event_bus is not None:
                        self._event_bus.publish(
                            PipelineEvent("page_failed", page_number, str(exc), 0.0)
                        )
                    self._log.warning("page_failed", page=page_number, error=str(exc))

        # Reassemble in page-number order.
        for raw_page in raw_pages:
            page_number = raw_page.number
            result = page_results[page_number]
            if self._event_bus is not None and not result.failures:
                self._event_bus.publish(
                    PipelineEvent("page_completed", page_number, duration_ms=0.0)
                )
            pages.append(result)
            self._maybe_checkpoint(checkpoint_id, pages)

        self._maybe_checkpoint(checkpoint_id, pages, force=True)
        return pages

    def _run_sequential(
        self,
        raw_pages: list[RawPage],
        ctx: TenantContext,
        pages: list[DocumentPage],
        checkpoint_id: str | None,
    ) -> list[DocumentPage]:
        """Process pages one at a time in the calling thread (original path)."""
        for raw_page in raw_pages:
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
                if self._event_bus is not None:
                    self._event_bus.publish(
                        PipelineEvent(
                            "page_failed",
                            raw_page.number,
                            str(exc),
                            (perf_counter() - started_at) * 1000,
                        )
                    )
                self._log.warning("page_failed", page=raw_page.number, error=str(exc))
            else:
                duration = (perf_counter() - started_at) * 1000
                if self._event_bus is not None:
                    self._event_bus.publish(
                        PipelineEvent(
                            "page_completed",
                            raw_page.number,
                            duration_ms=duration,
                        )
                    )
                self._log.info(
                    "page_completed", page=raw_page.number, duration_ms=round(duration, 1)
                )
            pages.append(page)

            if not self._maybe_checkpoint(checkpoint_id, pages):
                return pages  # return partial results on checkpoint failure

        self._maybe_checkpoint(checkpoint_id, pages, force=True)
        return pages

    def _maybe_checkpoint(
        self,
        checkpoint_id: str | None,
        pages: list[DocumentPage],
        force: bool = False,
    ) -> bool:
        """Persist progress every ``checkpoint_every`` pages. Returns success.

        Checkpointing serializes the whole accumulated document, so doing it
        after every page costs O(n²) I/O over a book — worst exactly where the
        target documents live (hundreds of pages). Batching trades at most
        ``checkpoint_every`` pages of redone work on a crash for linear I/O.
        ``force=True`` at the end of a run keeps the final checkpoint complete
        regardless of where the batch boundary fell.
        """
        if self._job_store is None or checkpoint_id is None:
            return True
        if not force and len(pages) % self._checkpoint_every != 0:
            return True
        checkpoint_result = self._job_store.checkpoint(
            checkpoint_id, DocumentStructure(pages=tuple(pages))
        )
        if isinstance(checkpoint_result, Err):
            self._log.error("checkpoint_failed", error=str(checkpoint_result.error))
            return False
        return True

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

        When ``max_workers > 1`` is configured, pages are collected from
        the source, submitted to a ``ThreadPoolExecutor``, and yielded in
        page-number order after all complete. This trades streaming
        progress for reduced wall-clock time on multi-page documents.

        Error behaviour: If the page source itself raises a
        ``PipelineError`` (e.g. corrupt document, I/O failure), the
        generator exits silently — no page is yielded for the failing
        stream position. Callers that need an explicit error signal
        should use ``run()`` instead, which returns a ``Result``.
        """
        ctx = context or TenantContext(
            organization_id="default", user_id="system", subscription_tier="desktop"
        )
        # Only the parallel path needs every page up front, to fan out. The
        # sequential path streams: materializing a 300-page scan here would
        # hold every page image in memory at once and defeat the point of a
        # page-streaming ingest, which is the whole reason this method exists.
        parallel = self._max_workers is not None and self._max_workers > 1
        try:
            if not parallel:
                yield from self._iterate_sequentially(self._page_source.stream(document), ctx)
                return
            raw_pages = list(self._page_source.stream(document))
        except PipelineError as exc:
            self._log.error("pipeline_failed", error=str(exc))
            return

        if len(raw_pages) > 1:
            page_results: dict[int, DocumentPage] = {}
            with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                future_to_page: dict[Future[DocumentPage], int] = {}
                for raw_page in raw_pages:
                    future = executor.submit(self._process_page, raw_page, ctx)
                    future_to_page[future] = raw_page.number

                for future in as_completed(future_to_page):
                    page_number = future_to_page[future]
                    try:
                        page_results[page_number] = future.result()
                    except Exception as exc:
                        raw = next(rp for rp in raw_pages if rp.number == page_number)
                        page_results[page_number] = DocumentPage(
                            number=page_number,
                            width=getattr(raw, "width", 1),
                            height=getattr(raw, "height", 1),
                            failures=(
                                PageFailure(error_type=type(exc).__name__, message=str(exc)),
                            ),
                        )
                        self._log.warning("page_failed", page=page_number, error=str(exc))

            # Yield in page-number order.
            for raw_page in raw_pages:
                page_number = raw_page.number
                page = page_results[page_number]
                self._log.info("page_completed", page=page_number, duration_ms=0.0)
                yield (page_number, page)
        else:
            yield from self._iterate_sequentially(raw_pages, ctx)

    def _iterate_sequentially(
        self, raw_pages: Iterable[RawPage], ctx: TenantContext
    ) -> Iterator[tuple[int, DocumentPage]]:
        """Yield one processed page at a time, consuming the source lazily.

        Takes an ``Iterable`` rather than a list so a generator streams through
        untouched — one page in memory at a time regardless of document size.
        """
        for raw_page in raw_pages:
            started_at = perf_counter()
            try:
                page = self._process_page(raw_page, ctx)
            except PipelineError as exc:
                page = DocumentPage(
                    number=raw_page.number,
                    width=getattr(raw_page, "width", 1),
                    height=getattr(raw_page, "height", 1),
                    failures=(PageFailure(error_type=type(exc).__name__, message=str(exc)),),
                )
                self._log.warning("page_failed", page=raw_page.number, error=str(exc))
            else:
                duration = (perf_counter() - started_at) * 1000
                self._log.info(
                    "page_completed", page=raw_page.number, duration_ms=round(duration, 1)
                )
            yield (raw_page.number, page)

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

    def _process_page(self, raw_page: RawPage, context: TenantContext) -> DocumentPage:
        variant_pages = self._variant_pages(raw_page, context)
        processed_page = variant_pages[0][1]

        segments = self._layout_analyzer.segment(processed_page, context)
        if isinstance(segments, Err):
            raise segments.error
        assert isinstance(segments, Ok)
        segment_values = segments.value

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
                            self._log.warning(
                                "engine_failed",
                                page=raw_page.number,
                                engine=getattr(engine, "name", type(engine).__name__),
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
        # raising, and that used to surface as `page N: ok (0 lines)` with
        # exit code 0 — a job could drop a third of a book and still look
        # clean to a scripted caller.
        failures: tuple[PageFailure, ...] = ()
        if not page_lines:
            failures = (
                PageFailure(
                    error_type="EmptyPage",
                    message="no text lines were produced for this page",
                ),
            )

        return DocumentPage(
            number=raw_page.number,
            width=getattr(raw_page, "width", 1),
            height=getattr(raw_page, "height", 1),
            lines=tuple(page_lines),
            suggestions=tuple(suggestions),
            failures=failures,
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
