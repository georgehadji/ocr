"""Embedded PDF text layer as a recognition source (docs/TEXT_LAYER_PLAN.md).

A born-digital PDF already carries exact characters with exact boxes.
Rasterising it and recognising the bitmap throws that away and replaces a CER
of 0.000 with the measured 0.1226. This module reads the layer instead — but
only where the layer is demonstrably the whole page.

**The presence test is the trap.** ``microsoft/markitdown`` implements exactly
that rule and would return this for page index 20 of the target document::

    — 21 —
    ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΣΑΛΟΝΙΚΗΣ

44 characters against a 2,973-character human transcription — CER 0.9939. The
book is a scan whose producer stamped a running head and folio onto every page
as real text. A presence test cannot tell that from a born-digital page, and
getting it wrong costs eight times more accuracy than the feature can win.

So two gates, both failing safe — a rejected page is rasterised and recognised
exactly as before, and the feature can only ever forgo a speedup:

``G1`` the page embeds **no raster image**. A page carrying one is a scan or a
figure, and in both the layer is at best partial. This is also the only defence
against PDFs where some other tool has already stamped invisible OCR text over
a full-page scan: those sail through any coverage test while carrying precisely
the low-quality recognition OmniOCR exists to beat.

``G2`` text line boxes cover at least ``TEXT_COVERAGE_MIN`` of the page.

Measured over all 74 pages of the target book on 2026-09-16, the two classes do
not overlap and are not close: its 62 scanned pages top out at ``txt_cov``
0.010, and its 5 born-digital pages run 0.433–0.552. The threshold sits in a
42x gap, not on a judgement call. That the same document contains both is why
the gate is per page rather than per document.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

from omniocr.domain.models import (
    AgreementTier,
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    OCRLine,
    Script,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, fitz is a runtime dependency
    import fitz

# Engine identity recorded on every line this module produces. Exports and the
# review UI already surface provenance, so this is what stops a searchable PDF
# from implying OmniOCR recognised text it copied.
ENGINE_NAME = "pdf_text_layer"

# G2. Ten times the worst observed scan page (0.010), four times below the
# worst real body page (0.433). Costs one page in the target book — a sparse
# title page at 0.063 — which is then recognised, the safe direction.
TEXT_COVERAGE_MIN = 0.10

# PDF user space is 1/72 inch, fixed by the spec. Duplicated from ingest rather
# than imported to keep the dependency one-way: ingest calls this module.
_PDF_POINTS_PER_INCH = 72

# One entry of `page.get_text("words")`: the word's rectangle in PDF points,
# its text, and the producer's own block / line / word numbering. fitz ships no
# type information, so the shape is declared here rather than inferred as Any.
#
# Read by unpacking rather than by index. Indexing with named constants is the
# obvious alternative and does not type-check: mypy cannot narrow a tuple
# element behind a variable subscript, so every coordinate comes back as
# `float | str | int` and needs a suppression at each use (CLAUDE.md rule 7).
Word = tuple[float, float, float, float, str, int, int, int]


def _run() -> EngineRun:
    """Provenance stamp. No model, no hash — nothing was recognised."""
    return EngineRun(
        engine=ENGINE_NAME,
        model_ref=ModelRef(engine=ENGINE_NAME, model_name="embedded", model_hash=""),
        model_hash="",
        params=(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _scaled_bbox(x0: float, y0: float, x1: float, y1: float, scale: float) -> BBox:
    """Map a PDF-point rectangle into rendered pixel space.

    The page is rasterised with ``fitz.Matrix(scale, scale)``, so boxes must be
    scaled by the same factor or they land nowhere near their glyphs. This is
    not cosmetic: the review UI overlays these on the rendered image and the
    searchable-PDF exporter positions its text from them.

    Width and height are floored at 1 because ``BBox`` rejects a zero
    dimension, and a PDF can legitimately contain a hairline-thin word box.
    """
    left, top = int(x0 * scale), int(y0 * scale)
    return BBox(
        x=max(0, left),
        y=max(0, top),
        w=max(1, int(x1 * scale) - left),
        h=max(1, int(y1 * scale) - top),
    )


def _has_raster_image(page: "fitz.Page") -> bool:
    """G1, via the only cheap call that answers it.

    ``get_images`` reads the resource dictionary — 9.9 ms/page on the target
    book. The two calls that would give image *placement* instead decode the
    image: ``get_image_rects`` costs 1,695 ms/page and ``get_text("dict")``
    costs 4,905 ms/page on the same pages. Neither is worth 170x for a
    refinement that would not change a single verdict on this document.
    """
    return bool(page.get_images(full=True))


def _line_bounds(words: Sequence[Word]) -> dict[tuple[int, int], list[float]]:
    """Group word rectangles into the producer's own lines, in PDF points."""
    lines: dict[tuple[int, int], list[float]] = {}
    for x0, y0, x1, y1, _, block_no, line_no, _ in words:
        bounds = lines.get((block_no, line_no))
        if bounds is None:
            lines[(block_no, line_no)] = [x0, y0, x1, y1]
            continue
        bounds[0] = min(bounds[0], x0)
        bounds[1] = min(bounds[1], y0)
        bounds[2] = max(bounds[2], x1)
        bounds[3] = max(bounds[3], y1)
    return lines


def _coverage(words: Sequence[Word], page_area: float) -> float:
    """Fraction of the page covered by text line boxes.

    Measured over lines rather than words so inter-word gaps count as covered:
    a line of text occupies its whole extent, and summing word boxes alone
    would read a normally-set page as sparse.
    """
    if page_area <= 0:
        return 0.0
    covered = sum(
        max(0.0, x1 - x0) * max(0.0, y1 - y0) for x0, y0, x1, y1 in _line_bounds(words).values()
    )
    return covered / page_area


def extract_text_layer(
    page: "fitz.Page", dpi: int, script: Script = Script.UNKNOWN
) -> tuple[OCRLine, ...]:
    """Lines from the page's embedded text, or ``()`` to recognise it instead.

    An empty result *is* the "recognise this page" signal. A rejected page is
    not an error and not an exceptional state — it is the overwhelmingly common
    case for this project's material — so it needs no second return value and
    no exception.

    Line breaking is the PDF producer's own: ``get_text("words")`` reports a
    ``(block_no, line_no)`` for every word, and grouping on that is the only
    mode that avoids substituting a layout heuristic of ours for the
    document's own structure. It is also the cheapest mode that carries boxes —
    33.6 ms on a born-digital page.

    ``script`` is the document-level variety the composition root already
    configures its layout analyzer with. It is passed rather than detected
    because these lines skip layout entirely, and without it every line would
    carry ``UNKNOWN``, whose lexicon ``SuggestOnlyCorrector`` has no entry for —
    born-digital pages would silently get a quieter review than recognized
    ones, which is the opposite of the intent.
    """
    # G1 first, and deliberately: on a scanned book it is the only check that
    # ever runs, so the whole feature costs 9.9 ms/page there rather than the
    # 188 ms a text extraction would add to every page of every scan.
    if _has_raster_image(page):
        return ()
    # A rotated page renders through a rotation the word coordinates do not
    # carry. The transform is three lines, but the target document is entirely
    # unrotated, so it would ship untested — and when a coordinate transform is
    # wrong it misplaces every box on the page while the text still reads
    # perfectly. Recognise those pages until a fixture proves the transform.
    if page.rotation:
        return ()

    words: Sequence[Word] = page.get_text("words")
    if not words:
        return ()
    rect = page.rect
    if _coverage(words, abs(rect.width * rect.height)) < TEXT_COVERAGE_MIN:
        return ()

    scale = dpi / _PDF_POINTS_PER_INCH
    page_number = page.number + 1
    run = _run()
    grouped: dict[tuple[int, int], list[OCRBlock]] = {}
    for index, (x0, y0, x1, y1, raw, block_no, line_no, _) in enumerate(words):
        text = unicodedata.normalize("NFC", raw)
        if not text.strip():
            continue
        grouped.setdefault((block_no, line_no), []).append(
            OCRBlock(
                id=f"p{page_number}-tl-w{index}",
                text=text,
                # Not an estimate. It records that no recognition happened, so
                # there is no recognition error to rank. These lines never
                # enter a vote — the pipeline short-circuits before routing —
                # so the number cannot outrank anything; it only keeps exact
                # text from being queued ahead of a contested line.
                confidence=Confidence(100.0),
                bbox=_scaled_bbox(x0, y0, x1, y1, scale),
                provenance=run,
            )
        )

    lines: list[OCRLine] = []
    for order, blocks in enumerate(grouped.values()):
        lines.append(
            OCRLine(
                id=f"p{page_number}-tl-l{order}",
                text=" ".join(block.text for block in blocks),
                confidence=Confidence(100.0),
                bbox=_union(blocks),
                script=script,
                reading_order=order,
                blocks=tuple(blocks),
                provenance=run,
                # One reading, stated honestly. Never gates export (A5).
                agreement=AgreementTier.SINGLE,
            )
        )
    return tuple(lines)


def _union(blocks: Sequence[OCRBlock]) -> BBox:
    left = min(block.bbox.x for block in blocks)
    top = min(block.bbox.y for block in blocks)
    right = max(block.bbox.right for block in blocks)
    bottom = max(block.bbox.bottom for block in blocks)
    return BBox(x=left, y=top, w=max(1, right - left), h=max(1, bottom - top))


__all__ = ["ENGINE_NAME", "TEXT_COVERAGE_MIN", "extract_text_layer"]
