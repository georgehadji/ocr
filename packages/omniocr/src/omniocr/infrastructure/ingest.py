from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Iterator

from omniocr.domain.errors import IngestError
from omniocr.domain.models import OCRLine, Script, UnreadablePage
from omniocr.infrastructure.text_layer import extract_text_layer

# Render resolution for every PDF page entering the pipeline. Tesseract and
# Kraken need this for box-grounded recognition; downstream consumers that do
# not (the VLM) downscale from it rather than re-deriving a number. Prose that
# quotes a DPI figure should cite this constant, not restate the digits.
RENDER_DPI = 300
# PDF user-space unit: 1/72 inch. Fixed by the PDF spec, not a tuning knob.
_PDF_POINTS_PER_INCH = 72


@dataclass(frozen=True, slots=True)
class ImagePage:
    number: int
    content: bytes
    width: int
    height: int
    # Lines read from the PDF's own embedded text, when that text passes the
    # coverage gate in ``text_layer`` (docs/TEXT_LAYER_PLAN.md). Empty for
    # every scan and every image, which is the overwhelmingly common case.
    #
    # Additive with a default, so ``frozen``/``slots`` survive and every
    # existing construction site keeps working — the pattern
    # ``OCRLine.agreement`` used for A5. It is deliberately *not* added to the
    # ``RawPage`` protocol: that is structural with four implementations, and
    # only the PDF source can ever populate this.
    text_lines: tuple[OCRLine, ...] = ()


class DocumentPageSource:
    """Lazily yield raster pages from a PDF or a single image document.

    A PDF page is always rasterised, even when its embedded text layer is used.
    The image is what a human reviews the text against (CLAUDE.md rule 1), and
    rendering is not the dominant cost of a run — recognition is. Skipping it
    would trade an unmeasured saving for a review UI with nothing to show.
    """

    def __init__(self, text_layer: bool = True, script: Script = Script.UNKNOWN) -> None:
        # The measurement escape hatch, not a feature toggle. The CER gate has
        # to be able to force recognition on a page that has a usable layer, or
        # it goes blind on mixed documents exactly the way it was blind to
        # preprocessing before A3 — a recognition regression on a born-digital
        # page would be invisible because recognition never ran.
        self._text_layer = text_layer
        # Stamped onto text-layer lines. These skip layout, which is where
        # the recognized path gets its script, so the composition root has
        # to pass the same value it configures the layout analyzer with or
        # the lexicon checks find no entry for the line and stay silent.
        self._script = script

    def stream(self, document: bytes) -> Iterator[ImagePage | UnreadablePage]:
        if document[:4] == b"%PDF":
            yield from self._stream_pdf(document)
            return
        yield from self._stream_image(document)

    def _stream_pdf(self, document: bytes) -> Iterator[ImagePage | UnreadablePage]:
        # Opening the file is one failure: nothing can be read from it. A page
        # that fails after that is its own failure, so one bad page does not
        # end the book.
        try:
            import fitz

            pdf = fitz.open(stream=document, filetype="pdf")
            matrix = fitz.Matrix(
                RENDER_DPI / _PDF_POINTS_PER_INCH, RENDER_DPI / _PDF_POINTS_PER_INCH
            )
            count = len(pdf)
        except Exception as exc:
            raise IngestError(f"PDF ingestion failed: {exc}") from exc
        try:
            for number in range(1, count + 1):
                try:
                    yield self._pdf_page(pdf, number, matrix)
                except Exception as exc:
                    yield UnreadablePage(number, f"PDF page {number} could not be read: {exc}")
        finally:
            pdf.close()

    def _pdf_page(self, pdf: Any, number: int, matrix: Any) -> ImagePage:
        page = pdf[number - 1]
        # Gated while the page is already open, so this costs no extra I/O and
        # 9.9 ms on a page that rejects.
        text_lines = extract_text_layer(page, RENDER_DPI, self._script) if self._text_layer else ()
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return ImagePage(number, pixmap.tobytes("png"), pixmap.width, pixmap.height, text_lines)

    @staticmethod
    def _stream_image(document: bytes) -> Iterator[ImagePage | UnreadablePage]:
        """One page per frame: a multi-page TIFF is a book, not one image."""
        try:
            from PIL import Image, ImageSequence

            image = Image.open(io.BytesIO(document))
            frames = getattr(image, "n_frames", 1)
        except Exception as exc:
            raise IngestError(f"image ingestion failed: {exc}") from exc
        if frames <= 1:
            yield ImagePage(1, document, image.width, image.height)
            return
        for number, frame in enumerate(ImageSequence.Iterator(image), start=1):
            try:
                # shortcut: modes PNG cannot hold (CMYK, 16-bit) are read as
                # RGB; use a wider encoder if 16-bit scans turn up.
                if frame.mode not in ("1", "L", "P", "RGB", "RGBA"):
                    frame = frame.convert("RGB")
                buffer = io.BytesIO()
                frame.save(buffer, format="PNG")
                yield ImagePage(number, buffer.getvalue(), frame.width, frame.height)
            except Exception as exc:
                yield UnreadablePage(number, f"image frame {number} could not be read: {exc}")


__all__ = ["DocumentPageSource", "ImagePage"]
