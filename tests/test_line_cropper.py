"""Tests for infrastructure/line_cropper.py — PIL-based line cropping."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from omniocr.domain.models import BBox
from omniocr.domain.result import Ok
from omniocr.infrastructure.line_cropper import PilLineCropper


@pytest.fixture
def page_image() -> bytes:
    """Create a simple test page image."""
    img = Image.new("RGB", (200, 100), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPilLineCropper:
    def test_basic_crop(self, page_image: bytes) -> None:
        cropper = PilLineCropper(padding=0)
        result = cropper.crop(page_image, BBox(x=10, y=10, w=50, h=20))
        assert isinstance(result, Ok)
        crop_bytes = result.value
        cropped = Image.open(BytesIO(crop_bytes))
        assert cropped.width == 50
        assert cropped.height == 20

    def test_crop_with_padding(self, page_image: bytes) -> None:
        cropper = PilLineCropper(padding=2)
        result = cropper.crop(page_image, BBox(x=10, y=10, w=50, h=20))
        assert isinstance(result, Ok)
        cropped = Image.open(BytesIO(result.value))
        assert cropped.width == 54  # 50 + 2*2 padding
        assert cropped.height == 24  # 20 + 2*2 padding

    def test_crop_clamps_to_bounds(self, page_image: bytes) -> None:
        """BBox near the edge should be clamped, not produce an error."""
        cropper = PilLineCropper(padding=10)
        result = cropper.crop(page_image, BBox(x=190, y=90, w=20, h=20))
        assert isinstance(result, Ok)
        cropped = Image.open(BytesIO(result.value))
        # Should be clamped to image bounds
        assert cropped.width > 0
        assert cropped.height > 0

    def test_empty_crop_after_clamp_raises(self, page_image: bytes) -> None:
        """A bbox completely outside the image should error."""
        cropper = PilLineCropper(padding=0)
        result = cropper.crop(page_image, BBox(x=500, y=500, w=10, h=10))
        assert isinstance(result, Ok) is False
        assert "empty crop" in str(result)

    def test_negative_padding_raises(self) -> None:
        with pytest.raises(ValueError, match="padding must not be negative"):
            PilLineCropper(padding=-1)
