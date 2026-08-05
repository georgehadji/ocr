import io

import pytest
from PIL import Image

from omniocr.domain.errors import IngestError
from omniocr.domain.models import TenantContext
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor, SauvolaProcessor


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(output, format="PNG")
    return output.getvalue()


def _pdf_bytes(page_count: int = 1) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page(width=72, height=72)
        page.insert_text((10, 36), "x")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_image_source_preserves_dimensions() -> None:
    page = next(DocumentPageSource().stream(_png_bytes()))
    assert (page.width, page.height) == (12, 8)


def test_grayscale_processor_emits_png_with_same_dimensions() -> None:
    page = next(DocumentPageSource().stream(_png_bytes()))
    processed = GrayscaleProcessor().process(page, TenantContext("org", "user", "desktop"))
    assert processed.is_ok()
    assert (processed.value.width, processed.value.height) == (12, 8)


def test_sauvola_processor_emits_binarized_png_with_same_dimensions() -> None:
    pytest.importorskip("cv2")
    page = next(DocumentPageSource().stream(_png_bytes()))

    processed = SauvolaProcessor().process(page, TenantContext("org", "user", "desktop"))

    assert processed.is_ok()
    assert (processed.value.width, processed.value.height) == (12, 8)
    assert processed.value.content != page.content


def test_pdf_source_streams_one_page_numbered_from_one() -> None:
    pytest.importorskip("fitz")

    pages = list(DocumentPageSource().stream(_pdf_bytes(page_count=1)))

    assert len(pages) == 1
    assert pages[0].number == 1
    assert pages[0].width > 0 and pages[0].height > 0


def test_pdf_source_streams_multiple_pages_in_order() -> None:
    pytest.importorskip("fitz")

    pages = list(DocumentPageSource().stream(_pdf_bytes(page_count=3)))

    assert [page.number for page in pages] == [1, 2, 3]


def test_pdf_source_raises_ingest_error_on_corrupt_pdf() -> None:
    pytest.importorskip("fitz")
    corrupt_pdf = b"%PDF-1.4\nthis is not actually a valid pdf body"

    with pytest.raises(IngestError, match="PDF ingestion failed"):
        list(DocumentPageSource().stream(corrupt_pdf))


def test_image_source_raises_ingest_error_on_undecodable_bytes() -> None:
    garbage = b"not an image and not a pdf either"

    with pytest.raises(IngestError, match="image ingestion failed"):
        list(DocumentPageSource().stream(garbage))
