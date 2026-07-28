"""End-to-end faithfulness tests (audit defect D8).

``test_faithfulness.py`` asserts that ``SuggestOnlyCorrector`` leaves
``line.text`` unchanged — but ``OCRLine`` is a frozen dataclass, so that
assertion cannot fail regardless of corrector behavior. It is tautological.

The actual product guarantee (ARCHITECTURE.md §1) is stronger: text
recognized by an engine must reach every export format **byte-identical**,
with post-correction contributing suggestions only. These tests drive a
full ``PipelineOrchestrator`` with an engine whose output is known in
advance, then assert that exact text survives to the exported bytes.

The stub engine returns deliberately hostile text: polytonic diacritics,
a real Pontian word a naive corrector would "fix" to standard Greek, a
Byzantine ligature, and a decomposed combining sequence.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import Sequence

import pytest

from omniocr.application.pipeline import InMemoryPage, PipelineOrchestrator
from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.application.router import ScriptRouter
from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    TenantContext,
)
from omniocr.domain.result import Ok, Result
from omniocr.infrastructure.exporters import (
    AltoXmlExporter,
    MarkdownExporter,
    PageXmlExporter,
    PlainTextExporter,
)
from omniocr.ports.interfaces import RawPage

# Text engineered to break a corrector that rewrites rather than suggests:
#   ἐν τῷ ὀνόματι — polytonic with breathings, accents, iota subscript
#   καλατσεύω     — genuine Pontian; standard Greek would "correct" it
#   ϗ             — Byzantine kai-ligature (U+03D7)
FAITHFUL_TEXT = "ἐν τῷ ὀνόματι ϗ καλατσεύω"

CONTEXT = TenantContext(organization_id="faithful", user_id="test", subscription_tier="desktop")


class _FixedTextEngine:
    """Engine returning known text, so exported bytes are fully predictable."""

    name = "fixed"

    def __init__(self, text: str) -> None:
        self._text = text

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        run = EngineRun(
            engine=self.name,
            model_ref=ModelRef(engine=self.name, model_name="fixed", model_hash="test", params=()),
            model_hash="test",
            params=(),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return Ok(
            (
                OCRBlock(
                    id="block-1",
                    text=self._text,
                    confidence=Confidence(90.0),
                    bbox=BBox(x=0, y=0, w=100, h=20),
                    provenance=run,
                ),
            )
        )


class _SinglePageSource:
    def __init__(self, content: bytes = b"page") -> None:
        self._content = content

    def stream(self, document: bytes):  # type: ignore[no-untyped-def]
        yield InMemoryPage(number=1, content=self._content, width=100, height=20)


def _pipeline(text: str, exporter: object) -> PipelineOrchestrator:
    engine = _FixedTextEngine(text)
    return PipelineOrchestrator(
        page_source=_SinglePageSource(),
        router=ScriptRouter(by_script={}, default=(engine,)),
        reconciler=ConfidenceWeightedReconciler(),
        exporter=exporter,  # type: ignore[arg-type]
    )


def _run_and_export(text: str, exporter: object) -> bytes:
    pipeline = _pipeline(text, exporter)
    result = pipeline.run(b"document", CONTEXT)
    assert result.is_ok(), f"pipeline failed: {result.error}"
    export = pipeline.export(result.value, CONTEXT)
    assert export.is_ok(), f"export failed: {export.error}"
    return bytes(export.value)


def test_recognized_text_reaches_document_structure_unchanged() -> None:
    """The engine's exact text must survive the pipeline into the document."""
    pipeline = _pipeline(FAITHFUL_TEXT, PlainTextExporter())
    result = pipeline.run(b"document", CONTEXT)

    assert result.is_ok()
    recognized = [line.text for page in result.value.pages for line in page.lines]
    assert FAITHFUL_TEXT in recognized, f"recognized text was altered by the pipeline: {recognized}"


@pytest.mark.parametrize(
    "exporter",
    [PlainTextExporter(), MarkdownExporter(), AltoXmlExporter(), PageXmlExporter()],
    ids=["txt", "markdown", "alto", "page-xml"],
)
def test_text_survives_every_export_byte_identical(exporter: object) -> None:
    """Every text-bearing export must contain the source bytes verbatim."""
    exported = _run_and_export(FAITHFUL_TEXT, exporter)

    assert FAITHFUL_TEXT.encode("utf-8") in exported, (
        f"{type(exporter).__name__} did not preserve source text byte-identically"
    )


def test_pontian_word_is_not_corrected_to_standard_greek() -> None:
    """A real Pontian word must not be rewritten en route to export.

    This is the faithfulness risk that matters most for the product: a
    corrector that "fixes" dialect into standard Greek destroys the
    scholarly value of the transcription.
    """
    exported = _run_and_export(FAITHFUL_TEXT, PlainTextExporter())

    assert "καλατσεύω".encode("utf-8") in exported
    assert "κουβεντιάζω".encode("utf-8") not in exported


def test_byzantine_ligature_is_not_expanded_by_default() -> None:
    """The kai-ligature must reach export intact, not silently expanded."""
    exported = _run_and_export(FAITHFUL_TEXT, PlainTextExporter())

    assert "ϗ".encode("utf-8") in exported, "Byzantine ligature was altered"


def test_polytonic_diacritics_are_preserved_exactly() -> None:
    """Breathing marks, accents, and iota subscript must survive intact."""
    exported = _run_and_export(FAITHFUL_TEXT, PlainTextExporter()).decode("utf-8")

    source_marks = [
        ch for ch in unicodedata.normalize("NFD", FAITHFUL_TEXT) if unicodedata.combining(ch)
    ]
    exported_marks = [
        ch for ch in unicodedata.normalize("NFD", exported) if unicodedata.combining(ch)
    ]

    assert source_marks, "test text has no combining marks — test would be vacuous"
    assert exported_marks == source_marks, "polytonic diacritics changed during export"


def test_decomposed_input_is_not_silently_recomposed_away() -> None:
    """NFC normalization is Unicode hygiene, never a loss of characters.

    Normalizing decomposed input is permitted, but the resulting text must
    still be canonically equivalent to what the engine produced.
    """
    decomposed = unicodedata.normalize("NFD", "ἀρχή")
    exported = _run_and_export(decomposed, PlainTextExporter()).decode("utf-8")

    assert (
        unicodedata.normalize("NFC", exported).find(unicodedata.normalize("NFC", decomposed)) != -1
    ), "decomposed text lost characters during the pipeline"
