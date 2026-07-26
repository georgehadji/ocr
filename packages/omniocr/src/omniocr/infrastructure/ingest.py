from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterator

from omniocr.domain.errors import IngestError


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
                matrix = fitz.Matrix(300 / 72, 300 / 72)
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
