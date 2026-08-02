"""Line cropper — adapter over PIL/OpenCV that crops a page image to a line-level region.

The concrete fix for D9: training samples reference line crops, never page images.
"""

from __future__ import annotations

from omniocr.domain.errors import TrainingError
from omniocr.domain.models import BBox
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ILineCropper


class PilLineCropper(ILineCropper):
    """Crop a page image to a line region using PIL.

    Crops by ``BBox`` with configurable padding (default 2px all sides).
    Output is PNG bytes.
    """

    def __init__(self, padding: int = 2) -> None:
        if padding < 0:
            raise ValueError("padding must not be negative")
        self._padding = padding

    def crop(self, page_image: bytes, bbox: BBox) -> Result[bytes, TrainingError]:
        """Crop a line region from a full-page image.

        Args:
            page_image: The full-page image bytes (PNG/JPEG/etc.).
            bbox: The bounding box of the line.

        Returns:
            Cropped PNG bytes of the line region, or an error.
        """
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(page_image))
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Apply padding, clamping to image bounds
            x = max(0, bbox.x - self._padding)
            y = max(0, bbox.y - self._padding)
            right = min(img.width, bbox.x + bbox.w + self._padding)
            bottom = min(img.height, bbox.y + bbox.h + self._padding)

            if right <= x or bottom <= y:
                return Err(
                    TrainingError(
                        f"empty crop region after clamping: ({x}, {y}, {right}, {bottom})"
                    )
                )

            cropped = img.crop((x, y, right, bottom))
            output = BytesIO()
            cropped.save(output, format="PNG")
            return Ok(output.getvalue())
        except ImportError:
            return Err(
                TrainingError(
                    "PIL (Pillow) is required for PilLineCropper. "
                    "Install with: pip install omniocr[tesseract]"
                )
            )
        except Exception as exc:
            return Err(TrainingError(f"crop failed: {exc}"))


__all__ = ["PilLineCropper"]
