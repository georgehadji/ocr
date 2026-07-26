from __future__ import annotations

import unicodedata

from omniocr.domain.models import TenantContext
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
            return Ok(type(page)(
                number=page.number,
                content=output.getvalue(),
                width=image.width,
                height=image.height,
            ))
        except Exception as exc:
            return Err(IngestError(f"image preprocessing failed: {exc}"))
