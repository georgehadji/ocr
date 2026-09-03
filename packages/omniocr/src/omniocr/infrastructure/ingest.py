from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterator

from omniocr.domain.errors import IngestError

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


class DocumentPageSource:
    """Lazily yield raster pages from a PDF or a single image document."""

    def stream(self, document: bytes) -> Iterator[ImagePage]:
        if document[:4] == b"%PDF":
            yield from self._stream_pdf(document)
            return
        yield self._image_page(document, 1)

    def _stream_pdf(self, document: bytes) -> Iterator[ImagePage]:
        try:
            import fitz

            pdf = fitz.open(stream=document, filetype="pdf")
            try:
                zoom = RENDER_DPI / _PDF_POINTS_PER_INCH
                matrix = fitz.Matrix(zoom, zoom)
                for number, page in enumerate(pdf, start=1):
                    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                    yield ImagePage(number, pixmap.tobytes("png"), pixmap.width, pixmap.height)
            finally:
                pdf.close()
        except Exception as exc:
            raise IngestError(f"PDF ingestion failed: {exc}") from exc

    @staticmethod
    def _image_page(document: bytes, number: int) -> ImagePage:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(document))
            return ImagePage(number, document, image.width, image.height)
        except Exception as exc:
            raise IngestError(f"image ingestion failed: {exc}") from exc


__all__ = ["DocumentPageSource", "ImagePage"]
