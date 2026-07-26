from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import unicodedata
from typing import Iterator, Mapping, Sequence
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
from omniocr.infrastructure.lexicon import SetLexicon
from omniocr.ports.interfaces import (
    IEventBus,
    IExporter,
    IImageProcessor,
    IJobStore,
    ILexicon,
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
    """Run conservative checks while preserving the recognized source text."""

    _BREATHING_MARKS: frozenset[str] = frozenset(
        ["\u0313", "\u0314"]  # smooth, rough
    )
    _ACCENT_MARKS: frozenset[str] = frozenset(
        ["\u0300", "\u0301", "\u0342"]  # grave, acute, circumflex/perispomeni
    )

    _ABBREVIATIONS: dict[str, str] = {
        "κ.τ.λ.": "καὶ τὰ λοιπά",
        "κ.λπ.": "καὶ λοιπά",
        "κ.α.": "καὶ ἄλλα",
        "κ.ο.κ.": "καὶ οὕτω καθεξῆς",
        "μ.Χ.": "μετὰ Χριστόν",
        "π.Χ.": "πρὸ Χριστοῦ",
        "π.χ.": "παραδείγματος χάριν",
        "βλ.": "βλέπε",
        "δηλ.": "δηλαδή",
        "στ.": "στίχος",
        "κεφ.": "κεφάλαιον",
        "σελ.": "σελίς",
        "τόμ.": "τόμος",
        "ἴδε": "ὅρα",
        "μτφ.": "μετάφρασις",
        "κ.ἐ.": "καὶ ἑξῆς",
        "κ.τ.τ.": "καὶ τὰ τοιαῦτα",
    }

    def __init__(
        self,
        lexicons: Mapping[Script, ILexicon] | None = None,
        ligatures: Mapping[str, str] | None = None,
        abbreviations: Mapping[str, str] | None = None,
    ) -> None:
        self._lexicons = dict(lexicons) if lexicons is not None else {}
        self._ligatures = dict(ligatures) if ligatures is not None else {}
        self._abbreviations = dict(abbreviations) if abbreviations is not None else SuggestOnlyCorrector._ABBREVIATIONS

    def correct(
        self, line: OCRLine, context: TenantContext
    ) -> Result[Sequence[Suggestion], EngineError]:
        suggestions: list[Suggestion] = []
        normalized = unicodedata.normalize("NFC", line.text)
        if normalized != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=normalized,
                    reason="unicode_nfc",
                    reversible=True,
                )
            )

        expanded = line.text
        for source, replacement in self._ligatures.items():
            expanded = expanded.replace(source, replacement)
        if expanded != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=expanded,
                    reason="reversible_ligature_expansion",
                    reversible=True,
                )
            )

        expanded_text = line.text
        for abbr, expansion in self._abbreviations.items():
            if abbr in line.text:
                expanded_text = expanded_text.replace(abbr, expansion)
        if expanded_text != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=expanded_text,
                    reason="reversible_abbreviation_expansion",
                    reversible=True,
                )
            )

        for index, character in enumerate(line.text):
            if not unicodedata.category(character).startswith("M"):
                continue
            previous_category = unicodedata.category(line.text[index - 1]) if index else ""
            if previous_category.startswith(("L", "M")):
                continue
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=line.text,
                    reason="dangling_combining_mark",
                    reversible=True,
                )
            )

        lexicon = self._lexicons.get(line.script)
        if lexicon is not None:
            for token in line.text.split():
                if token and not lexicon.contains(token):
                    suggestions.append(
                        Suggestion(
                            line_id=line.id,
                            source_text=token,
                            suggestion_text=token,
                            reason=f"not_in_{lexicon.name}_lexicon",
                            reversible=True,
                        )
                    )

        diacritic_issues = self._check_diacritics(line.text)
        for issue in diacritic_issues:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=line.text,
                    reason=issue,
                    reversible=True,
                )
            )

        return Ok(tuple(suggestions))

    @staticmethod
    def _check_diacritics(text: str) -> list[str]:
        """Check for impossible polytonic diacritic combinations.

        Returns a list of reason strings, one per detected violation.
        """
        issues: list[str] = []
        nfd = unicodedata.normalize("NFD", text)
        index = 0
        while index < len(nfd):
            character = nfd[index]
            if unicodedata.category(character) in ("Mn", "Mc"):
                index += 1
                continue
            # Count combining marks on this base character
            breathing = 0
            accents = 0
            index += 1
            while index < len(nfd) and unicodedata.category(nfd[index]) in ("Mn", "Mc"):
                mark = nfd[index]
                if mark in SuggestOnlyCorrector._BREATHING_MARKS:
                    breathing += 1
                elif mark in SuggestOnlyCorrector._ACCENT_MARKS:
                    accents += 1
                index += 1
            if breathing > 1:
                issues.append("multiple_breathing_marks")
            if accents > 1:
                issues.append("multiple_accent_marks")
        return issues


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
        event_bus: IEventBus | None = None,
    ) -> None:
        self._page_source = page_source or NullPageSource()
        self._image_processor = image_processor or PassthroughImageProcessor()
        self._layout_analyzer = layout_analyzer or SingleLineLayoutAnalyzer()
        self._router = router or NullRouter()
        self._reconciler = reconciler or FirstCandidateReconciler()
        self._post_corrector = post_corrector or SuggestOnlyCorrector()
        self._exporter = exporter or PlainTextExporter()
        self._job_store = job_store
        self._event_bus = event_bus

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
        try:
            page_stream = self._page_source.stream(document)
            for raw_page in page_stream:
                if raw_page.number in completed_numbers:
                    continue
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
                    if self._event_bus is not None:
                        self._event_bus.publish(
                            PipelineEvent(
                                "page_failed",
                                raw_page.number,
                                str(exc),
                                (perf_counter() - started_at) * 1000,
                            )
                        )
                else:
                    if self._event_bus is not None:
                        self._event_bus.publish(
                            PipelineEvent(
                                "page_completed",
                                raw_page.number,
                                duration_ms=(perf_counter() - started_at) * 1000,
                            )
                        )
                pages.append(page)
                completed_numbers.add(raw_page.number)

                if self._job_store is not None and checkpoint_id is not None:
                    checkpoint_result = self._job_store.checkpoint(
                        checkpoint_id, DocumentStructure(pages=tuple(pages))
                    )
                    if isinstance(checkpoint_result, Err):
                        return Err(checkpoint_result.error)
        except PipelineError as exc:
            return Err(exc)

        return Ok(DocumentStructure(pages=tuple(pages)))

    def _process_page(self, raw_page: RawPage, context: TenantContext) -> DocumentPage:
        processed = self._image_processor.process(raw_page, context)
        if isinstance(processed, Err):
            raise processed.error
        assert isinstance(processed, Ok)
        processed_page = processed.value

        segments = self._layout_analyzer.segment(processed_page, context)
        if isinstance(segments, Err):
            raise segments.error
        assert isinstance(segments, Ok)
        segment_values = segments.value

        page_lines: list[OCRLine] = []
        suggestions: list[Suggestion] = []
        engine_results: dict[int, Sequence[OCRBlock]] = {}
        for segment in segment_values:
            engines = self._router.route(segment, context)
            candidate_lines: list[OCRLine] = []
            for engine in engines:
                engine_key = id(engine)
                if engine_key not in engine_results:
                    extracted = engine.extract(processed_page, context)
                    engine_results[engine_key] = (
                        extracted.value if isinstance(extracted, Ok) else ()
                    )
                blocks = engine_results[engine_key]
                for block in blocks:
                    if not self._boxes_overlap(segment.bbox, block.bbox):
                        continue
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
            chosen = self._reconciler.reconcile(candidate_lines, context)
            if isinstance(chosen, Err):
                raise chosen.error
            assert isinstance(chosen, Ok)
            chosen_line = chosen.value
            corrections = self._post_corrector.correct(chosen_line, context)
            if isinstance(corrections, Err):
                raise corrections.error
            assert isinstance(corrections, Ok)
            page_lines.append(chosen_line)
            suggestions.extend(corrections.value)

        return DocumentPage(
            number=raw_page.number,
            width=getattr(raw_page, "width", 1),
            height=getattr(raw_page, "height", 1),
            lines=tuple(page_lines),
            suggestions=tuple(suggestions),
        )

    @staticmethod
    def _boxes_overlap(first: BBox, second: BBox) -> bool:
        return (
            first.x < second.right
            and second.x < first.right
            and first.y < second.bottom
            and second.y < first.bottom
        )

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
