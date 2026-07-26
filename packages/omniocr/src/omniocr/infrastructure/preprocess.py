from __future__ import annotations

import unicodedata

from omniocr.domain.models import TenantContext
from omniocr.infrastructure.ingest import ImagePage
import io

from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.errors import IngestError
from omniocr.ports.interfaces import RawPage


def normalize_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class PassthroughProcessor:
    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Ok(page)


class GrayscaleProcessor:
    """Decode a raster page, convert it to grayscale, and emit PNG bytes."""

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(page.content)).convert("L")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=output.getvalue(),
                    width=image.width,
                    height=image.height,
                )
            )
        except Exception as exc:
            return Err(IngestError(f"image preprocessing failed: {exc}"))


class SauvolaProcessor:
    """Apply local adaptive binarization for degraded printed pages.

    OpenCV is imported only when this adapter is used. Kraken/VLM callers should
    generally keep the grayscale page instead of applying this high-contrast filter.
    """

    def __init__(self, window_size: int = 11, constant: float = 2.0) -> None:
        if window_size < 3 or window_size % 2 == 0:
            raise ValueError("window_size must be an odd integer greater than or equal to 3")
        self._window_size = window_size
        self._constant = constant

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("page content is not a decodable raster image")
            binary = cv2.adaptiveThreshold(
                image,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                self._window_size,
                self._constant,
            )
            encoded, content = cv2.imencode(".png", binary)
            if not encoded:
                raise ValueError("OpenCV could not encode the binarized page")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=content.tobytes(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
            )
        except Exception as exc:
            return Err(IngestError(f"adaptive binarization failed: {exc}"))


__all__ = ["GrayscaleProcessor", "PassthroughProcessor", "SauvolaProcessor", "normalize_nfc"]
