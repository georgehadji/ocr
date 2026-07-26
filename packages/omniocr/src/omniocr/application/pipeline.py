from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Iterator, Sequence
from omniocr.domain.errors import EngineError, ExportError, IngestError, LayoutError, PipelineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    Script,
    Suggestion,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import (
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


class FirstCandidateReconciler:
    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]:
        if not candidates:
            return Err(EngineError("no OCR candidates available"))
        return Ok(candidates[0])


class SuggestOnlyCorrector:
    def correct(
        self, line: OCRLine, context: TenantContext
    ) -> Result[Sequence[Suggestion], EngineError]:
        normalized = unicodedata.normalize("NFC", line.text)
        if normalized == line.text:
            return Ok(())
        suggestion = Suggestion(
            line_id=line.id,
            source_text=line.text,
            suggestion_text=normalized,
            reason="unicode_nfc",
            reversible=True,
        )
        return Ok((suggestion,))


class PlainTextExporter:
    def export(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        lines = [line.text for page in document.pages for line in page.lines]
        return Ok("\n".join(lines).encode("utf-8"))


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
        job_store: IJobStore | None = None,
    ) -> None:
        self._page_source = page_source or NullPageSource()
        self._image_processor = image_processor or PassthroughImageProcessor()
        self._layout_analyzer = layout_analyzer or SingleLineLayoutAnalyzer()
        self._router = router or NullRouter()
        self._reconciler = reconciler or FirstCandidateReconciler()
        self._post_corrector = post_corrector or SuggestOnlyCorrector()
        self._exporter = exporter or PlainTextExporter()
        self._job_store = job_store

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
        checkpoint = (
            self._job_store.load(resume_job_id or job_id)
            if self._job_store is not None and (resume_job_id or job_id) is not None
            else None
        )
        pages: list[DocumentPage] = list(checkpoint.pages) if checkpoint is not None else []
        completed_numbers = {page.number for page in pages}
        try:
            page_stream = self._page_source.stream(document)
            for raw_page in page_stream:
                if raw_page.number in completed_numbers:
                    continue
                processed = self._image_processor.process(raw_page, ctx)
                if processed.is_err():
                    return Err(processed.error)

                segments = self._layout_analyzer.segment(processed.value, ctx)
                if segments.is_err():
                    return Err(segments.error)

                page_lines: list[OCRLine] = []
                suggestions: list[Suggestion] = []
                for segment in segments.value:
                    engines = self._router.route(segment, ctx)
                    candidate_lines: list[OCRLine] = []
                    for engine in engines:
                        extracted = engine.extract(processed.value, ctx)
                        if extracted.is_err():
                            continue
                        for block in extracted.value:
                            candidate_lines.append(
                                OCRLine(
                                    id=f"{segment.id}-{block.id}",
                                    text=block.text,
                                    confidence=block.confidence,
                                    bbox=block.bbox,
                                    script=segment.script,
                                    blocks=(block,),
                                    provenance=block.provenance,
                                )
                            )
                    if not candidate_lines:
                        candidate_lines.append(segment)
                    chosen = self._reconciler.reconcile(candidate_lines, ctx)
                    if chosen.is_err():
                        return Err(chosen.error)
                    corrections = self._post_corrector.correct(chosen.value, ctx)
                    if corrections.is_err():
                        return Err(corrections.error)
                    page_lines.append(chosen.value)
                    suggestions.extend(corrections.value)

                pages.append(
                    DocumentPage(
                        number=raw_page.number,
                        width=getattr(raw_page, "width", 1),
                        height=getattr(raw_page, "height", 1),
                        lines=tuple(page_lines),
                        suggestions=tuple(suggestions),
                    )
                )

                if self._job_store is not None and checkpoint_id is not None:
                    checkpoint = self._job_store.checkpoint(
                        checkpoint_id, DocumentStructure(pages=tuple(pages))
                    )
                    if checkpoint.is_err():
                        return Err(checkpoint.error)
        except PipelineError as exc:
            return Err(exc)

        return Ok(DocumentStructure(pages=tuple(pages)))

    def export(
        self, document: DocumentStructure, context: TenantContext | None = None
    ) -> Result[bytes, PipelineError]:
        ctx = context or TenantContext(
            organization_id="default", user_id="system", subscription_tier="desktop"
        )
        result = self._exporter.export(document, ctx)
        if result.is_err():
            return Err(result.error)
        return Ok(result.value)
