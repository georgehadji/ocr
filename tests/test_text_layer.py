"""Tests for the embedded PDF text layer (docs/TEXT_LAYER_PLAN.md).

Every fixture is built in-process with ``fitz``; none needs the copyrighted
source document.

Most of the weight is on the **gate**, because the failure it prevents is the
expensive one. Trusting a partial layer on the target book returns a
44-character running head against a 2,973-character page — CER 0.9939, against
the 0.1226 recognition already achieves. Rejecting a usable layer merely
forgoes a speedup.
"""

from __future__ import annotations

import io
import unicodedata
from typing import Any, Sequence

import pytest

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.domain.errors import EngineError
from omniocr.domain.models import AgreementTier, OCRBlock, OCRLine, Script, TenantContext
from omniocr.domain.result import Ok, Result
from omniocr.infrastructure.ingest import RENDER_DPI, DocumentPageSource
from omniocr.infrastructure.text_layer import (
    ENGINE_NAME,
    TEXT_COVERAGE_MIN,
    extract_text_layer,
)
from omniocr.ports.interfaces import RawPage
from omniocr.ports.lexicon import SetLexicon

fitz = pytest.importorskip("fitz")

CONTEXT = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")

# Roughly the target book's page box, so coverage figures here are comparable
# with the ones measured on it.
PAGE_WIDTH, PAGE_HEIGHT = 482.0, 680.0

GREEK_LINE = "Τὸ βυζαντινὸν μνημεῖον τῆς Θεσσαλονίκης καὶ ἡ ἱστορία του."


def _write(page: Any, items: Sequence[tuple[float, float, str, float]]) -> None:
    """Draw text through ``TextWriter`` so the glyphs are really embedded.

    ``insert_text`` with a Base-14 name encodes as WinAnsi and silently turns
    every Greek letter into U+00B7 — the page renders as rows of dots and
    extraction returns them. The first version of these tests did that and
    "passed" the geometry checks against punctuation. ``TextWriter`` subsets
    the actual glyphs from MuPDF's builtin font, which carries Greek, so the
    fixture needs no system font and stays portable to CI.
    """
    writer = fitz.TextWriter(page.rect)
    font = fitz.Font("helv")
    for x, y, text, size in items:
        writer.append((x, y), text, font=font, fontsize=size)
    writer.write_text(page)


def _write_lines(page: Any, count: int) -> None:
    _write(page, [(40.0, 60.0 + 18.0 * index, GREEK_LINE, 11.0) for index in range(count)])


def _born_digital(lines: int = 32, page_count: int = 1, rotation: int = 0) -> Any:
    """A page of real text and nothing else — the case the feature exists for."""
    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        _write_lines(page, lines)
        if rotation:
            page.set_rotation(rotation)
    return doc


def _render(page: Any) -> Any:
    """The page as the pipeline rasterises it, opened for pixel inspection."""
    from PIL import Image

    pixmap = page.get_pixmap(matrix=fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72))
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")


def _blank_pixmap() -> Any:
    pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 600, 850), False)
    pixmap.clear_with(255)
    return pixmap


def _scanned(text_lines: int = 0) -> Any:
    """A full-page image with text stamped on top.

    With ``text_lines=0`` this is the target book's trap reproduced at the
    measured size: a folio and a running head, 44 characters over a scan.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, pixmap=_blank_pixmap())
    _write(
        page,
        [
            (216.0, 620.0, "— 21 —", 9.0),
            (116.0, 40.0, "ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΣΑΛΟΝΙΚΗΣ", 9.0),
        ],
    )
    _write_lines(page, text_lines)
    return doc


class TestGate:
    def test_a_born_digital_page_is_accepted(self) -> None:
        with _born_digital() as doc:
            assert extract_text_layer(doc[0], RENDER_DPI)

    def test_a_running_head_over_a_scan_is_rejected(self) -> None:
        """The whole reason this module has a gate rather than a presence test."""
        with _scanned() as doc:
            assert extract_text_layer(doc[0], RENDER_DPI) == ()

    def test_a_full_page_image_is_rejected_even_under_dense_text(self) -> None:
        """G1, and the only defence against an already-OCR'd scan.

        A PDF that some other tool has stamped invisible OCR text over passes
        any coverage test while carrying exactly the low-quality recognition
        this project exists to beat. Coverage cannot see that; the image can.
        """
        with _scanned(text_lines=32) as doc:
            assert extract_text_layer(doc[0], RENDER_DPI) == ()

    @pytest.mark.parametrize("rotation", [90, 180, 270])
    def test_a_rotated_page_is_read_not_refused(self, rotation: int) -> None:
        with _born_digital(rotation=rotation) as doc:
            assert extract_text_layer(doc[0], RENDER_DPI)

    def test_an_empty_page_is_rejected(self) -> None:
        doc = fitz.open()
        doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        assert extract_text_layer(doc[0], RENDER_DPI) == ()
        doc.close()

    def test_the_threshold_decides_and_nothing_else_does(self) -> None:
        """Brackets the constant: one line fewer is recognized, one more is read.

        Line count is the control, because each line contributes a
        near-constant share of page area. Walking coverage across the threshold
        beats asserting a hand-picked number that a font metric would
        invalidate.
        """
        accepted = [count for count in range(1, 60) if self._accepts(count)]
        assert accepted, "no line count was ever accepted — the fixture is wrong"
        first = accepted[0]
        assert first > 1, "a single line should not clear a page-coverage gate"
        assert not self._accepts(first - 1)
        assert accepted == list(range(first, 60)), "acceptance must be monotone in coverage"
        assert 0.0 < TEXT_COVERAGE_MIN < 1.0

    @staticmethod
    def _accepts(lines: int) -> bool:
        with _born_digital(lines=lines) as doc:
            return bool(extract_text_layer(doc[0], RENDER_DPI))


class TestExtraction:
    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_boxes_are_scaled_into_rendered_pixel_space(self, rotation: int) -> None:
        with _born_digital(rotation=rotation) as doc:
            page = doc[0]
            lines = extract_text_layer(page, RENDER_DPI)
            rendered = _render(page)

        assert lines
        for line in lines:
            assert line.bbox.right <= rendered.width
            assert line.bbox.bottom <= rendered.height
        # A box left in PDF points would sit in the top-left eighth of a
        # 300-DPI render, so this fails loudly if the scale is dropped.
        assert max(line.bbox.bottom for line in lines) > rendered.height / 2

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_boxes_land_on_ink(self, rotation: int) -> None:
        """The check a mere in-bounds assertion cannot make.

        A wrong scale factor, or a missing rotation transform, can keep every
        box inside the page and still put all of them on blank paper. Crop each
        reported box out of the render and require something darker than paper.

        This is the whole reason rotation support has a fixture. Measured
        2026-09-17 before the transform went in: on a 90-degree page the raw
        coordinates read luminance 255 — paper — while the mapped ones read 0,
        and every other signal, the text included, looked perfect.
        """
        with _born_digital(rotation=rotation) as doc:
            page = doc[0]
            lines = extract_text_layer(page, RENDER_DPI)
            rendered = _render(page)

        assert lines
        for line in lines:
            box = line.bbox
            crop = rendered.crop((box.x, box.y, box.right, box.bottom))
            darkest, _ = crop.getextrema()
            assert darkest < 200, f"no ink inside {line.id} at {box} (rotation {rotation})"

    @pytest.mark.parametrize("rotation", [90, 180, 270])
    def test_rotating_a_page_does_not_change_its_text(self, rotation: int) -> None:
        """Rotation moves boxes, never characters."""
        with _born_digital() as upright, _born_digital(rotation=rotation) as turned:
            straight = extract_text_layer(upright[0], RENDER_DPI)
            rotated = extract_text_layer(turned[0], RENDER_DPI)

        assert [line.text for line in rotated] == [line.text for line in straight]

    def test_one_line_per_source_line_with_word_blocks(self) -> None:
        with _born_digital(lines=20) as doc:
            lines = extract_text_layer(doc[0], RENDER_DPI)

        assert len(lines) == 20
        for line in lines:
            assert line.text == GREEK_LINE
            assert len(line.blocks) == len(GREEK_LINE.split())

    def test_text_is_nfc_normalized(self) -> None:
        """CLAUDE.md rule 5 — Unicode hygiene, not an orthographic change."""
        with _born_digital() as doc:
            lines = extract_text_layer(doc[0], RENDER_DPI)

        for line in lines:
            assert line.text == unicodedata.normalize("NFC", line.text)

    def test_provenance_says_the_text_was_copied_not_recognized(self) -> None:
        """An export must never imply OmniOCR recognized what it copied."""
        with _born_digital() as doc:
            lines = extract_text_layer(doc[0], RENDER_DPI)

        for line in lines:
            assert line.provenance is not None
            assert line.provenance.engine == ENGINE_NAME
            assert line.provenance.model_ref.engine == ENGINE_NAME
            assert line.agreement is AgreementTier.SINGLE


class TestIngest:
    def test_a_born_digital_pdf_carries_its_text_lines(self) -> None:
        with _born_digital(page_count=2) as doc:
            document = doc.tobytes()

        pages = list(DocumentPageSource().stream(document))

        assert len(pages) == 2
        for page in pages:
            assert page.text_lines
            # Still rasterized: the human reviews the text against the image.
            assert page.content[:4] == b"\x89PNG"

    def test_a_scanned_pdf_carries_none(self) -> None:
        with _scanned() as doc:
            document = doc.tobytes()

        page = next(iter(DocumentPageSource().stream(document)))

        assert page.text_lines == ()

    def test_the_switch_turns_it_off(self) -> None:
        with _born_digital() as doc:
            document = doc.tobytes()

        page = next(iter(DocumentPageSource(text_layer=False).stream(document)))

        assert page.text_lines == ()
        assert page.content[:4] == b"\x89PNG"


class _RecordingEngine:
    """Counts extraction calls. "It was fast" is not a test."""

    name = "recording"

    def __init__(self) -> None:
        self.calls = 0

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        self.calls += 1
        return Ok(())


class _SingleEngineRouter:
    def __init__(self, engine: _RecordingEngine) -> None:
        self._engine = engine

    def route(self, line: OCRLine, context: TenantContext) -> tuple[_RecordingEngine, ...]:
        return (self._engine,)


def _orchestrator(engine: _RecordingEngine, text_layer: bool = True) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        page_source=DocumentPageSource(text_layer=text_layer),
        router=_SingleEngineRouter(engine),
    )


class TestPipeline:
    def test_a_born_digital_page_skips_every_engine(self) -> None:
        engine = _RecordingEngine()
        with _born_digital(lines=20) as doc:
            document = doc.tobytes()

        result = _orchestrator(engine).run(document, CONTEXT)

        assert result.is_ok()
        page = result.value.pages[0]
        assert engine.calls == 0
        assert page.lines
        assert not page.failures
        assert all(
            line.provenance is not None and line.provenance.engine == ENGINE_NAME
            for line in page.lines
        )

    def test_the_same_page_routes_to_engines_when_the_layer_is_refused(self) -> None:
        """The control: nothing about the page changed except the switch."""
        engine = _RecordingEngine()
        with _born_digital(lines=20) as doc:
            document = doc.tobytes()

        _orchestrator(engine, text_layer=False).run(document, CONTEXT)

        assert engine.calls > 0

    def test_a_scanned_page_still_routes_to_engines(self) -> None:
        engine = _RecordingEngine()
        with _scanned() as doc:
            document = doc.tobytes()

        _orchestrator(engine).run(document, CONTEXT)

        assert engine.calls > 0

    def test_the_configured_script_reaches_the_lines(self) -> None:
        """Without it every line is ``UNKNOWN`` and the lexicon checks go quiet.

        These lines skip layout, which is where the recognized path picks up
        its script, so the value has to be threaded from the composition root.
        """
        with _born_digital() as doc:
            document = doc.tobytes()

        page = next(iter(DocumentPageSource(script=Script.POLYTONIC).stream(document)))

        assert page.text_lines
        assert all(line.script is Script.POLYTONIC for line in page.text_lines)

    def test_post_correction_still_runs_on_text_layer_pages(self) -> None:
        """Skipping it would give these pages a quieter review than recognized ones."""
        with _born_digital(lines=20) as doc:
            document = doc.tobytes()

        orchestrator = PipelineOrchestrator(
            page_source=DocumentPageSource(script=Script.POLYTONIC),
            post_corrector=SuggestOnlyCorrector(
                lexicons={Script.POLYTONIC: SetLexicon(name="empty", words=[])}
            ),
        )
        result = orchestrator.run(document, CONTEXT)

        assert result.is_ok()
        page = result.value.pages[0]
        assert "not_in_empty_lexicon" in {s.reason for s in page.suggestions}
        # Suggest-only: the text itself is untouched (CLAUDE.md rule 1).
        assert all(line.text == GREEK_LINE for line in page.lines)
