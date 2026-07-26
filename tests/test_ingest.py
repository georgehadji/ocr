import io

from PIL import Image

from omniocr.domain.models import TenantContext
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, format="PNG")
    return output.getvalue()


def test_image_source_preserves_dimensions() -> None:
    page = next(DocumentPageSource().stream(_png_bytes()))
    assert (page.width, page.height) == (12, 8)


def test_grayscale_processor_emits_png_with_same_dimensions() -> None:
    page = next(DocumentPageSource().stream(_png_bytes()))
    processed = GrayscaleProcessor().process(page, TenantContext("org", "user", "desktop"))
    assert processed.is_ok()
    assert (processed.value.width, processed.value.height) == (12, 8)
