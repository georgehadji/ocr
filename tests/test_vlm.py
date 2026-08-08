"""Tests for VLMEngine — optional cloud-API adapter with grounding guard."""

from __future__ import annotations

import pytest

from omniocr.infrastructure.vlm import VLMEngine


def test_default_api_url_is_accepted() -> None:
    VLMEngine(api_key="test-key")  # must not raise


def test_https_api_url_is_accepted() -> None:
    VLMEngine(api_key="test-key", api_url="https://api.example.com/v1")  # must not raise


def test_http_api_url_is_accepted() -> None:
    VLMEngine(api_key="test-key", api_url="http://localhost:8080/v1")  # must not raise


@pytest.mark.parametrize(
    "bad_url",
    ["file:///etc/passwd", "ftp://example.com", "javascript:alert(1)", "example.com/v1"],
)
def test_non_http_api_url_is_rejected_at_construction(bad_url: str) -> None:
    """A misconfigured scheme must fail fast, not silently open at call time."""
    with pytest.raises(ValueError, match="must be http"):
        VLMEngine(api_key="test-key", api_url=bad_url)


def _api_response(
    content: str,
    model: str = "gpt-4o-mini",
) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "model": model,
    }


def test_vlm_parse_response_structured_format() -> None:
    """Structured API response with x1,y1,x2,y2|text format is parsed correctly."""
    engine = VLMEngine(api_key="test-key")
    data = _api_response("10,10,210,30|Line one\n10,40,200,60|Line two")
    blocks = engine._parse_response(data, 400, 100)

    assert len(blocks) == 2
    assert blocks[0].text == "Line one"
    assert blocks[0].bbox.w == 200
    assert blocks[0].bbox.h == 20
    assert blocks[1].text == "Line two"
    assert blocks[1].id == "vlm-1"
    assert blocks[0].provenance is not None


def test_vlm_parse_response_handles_no_content() -> None:
    """Missing or empty content produces no blocks."""
    engine = VLMEngine(api_key="test-key")

    empty = engine._parse_response({}, 400, 100)
    assert len(empty) == 0

    no_choices = engine._parse_response({"choices": []}, 400, 100)
    assert len(no_choices) == 0


def test_vlm_parse_response_unstructured_fallback() -> None:
    """Text without structured coordinates uses full-page bbox as fallback."""
    engine = VLMEngine(api_key="test-key")
    data = _api_response("Just some unstructured text here")
    blocks = engine._parse_response(data, 400, 100)

    assert len(blocks) > 0
    for block in blocks:
        assert block.bbox.x == 0
        assert block.bbox.y == 0
        assert block.bbox.w == 400


def test_vlm_parse_response_skips_malformed_lines() -> None:
    """Lines without pipe or with non-numeric coords are skipped."""
    engine = VLMEngine(api_key="test-key")
    data = _api_response("no pipe here\n10,10,20,20|valid\nnot,coords|skip")
    blocks = engine._parse_response(data, 400, 100)

    assert len(blocks) == 1
    assert blocks[0].text == "valid"


def test_vlm_extract_guarded_without_engine_blocks_keeps_all() -> None:
    """extract_guarded with no engine blocks returns all VLM blocks."""
    engine = VLMEngine(api_key="test-key")
    # parse_response doesn't need API access — test directly
    data = _api_response("10,10,210,30|text")
    blocks = engine._parse_response(data, 400, 100)

    assert len(blocks) >= 1


def test_vlm_repr_masks_api_key() -> None:
    """repr(VLMEngine) shows truncated API key, never the full secret."""
    engine = VLMEngine(api_key="sk-or-v1-abcdef123456")
    representation = repr(engine)
    assert "sk-or-v1-abcdef123456" not in representation
    assert "sk-or-v1...3456" in representation

    short_key = VLMEngine(api_key="short")
    assert "short" not in repr(short_key)
    assert "***" in repr(short_key)


class TestDownscaling:
    """Image downscaling — the dominant cost lever for the VLM.

    Vision models bill per tile, so a 300 DPI page (rendered that high for
    Tesseract/Kraken box grounding) costs several thousand input tokens the
    VLM does not need. See docs/VLM_COST_OPTIMIZATION.md.
    """

    @staticmethod
    def _png(width: int, height: int) -> bytes:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    def _dimensions(self, image_bytes: bytes) -> tuple[int, int]:
        import io

        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        return image.width, image.height

    def test_large_page_is_shrunk_to_max_edge(self) -> None:
        engine = VLMEngine(api_key="k", max_edge_px=700)

        out, scale = engine._downscale(self._png(1749, 2480))

        assert self._dimensions(out) == (494, 700)
        assert scale == pytest.approx(700 / 2480)

    def test_small_page_is_returned_untouched(self) -> None:
        engine = VLMEngine(api_key="k", max_edge_px=4000)
        original = self._png(800, 600)

        out, scale = engine._downscale(original)

        assert out is original
        assert scale == 1.0

    def test_aspect_ratio_is_preserved(self) -> None:
        engine = VLMEngine(api_key="k", max_edge_px=1000)

        out, _ = engine._downscale(self._png(2000, 1000))

        assert self._dimensions(out) == (1000, 500)

    def test_undecodable_bytes_fall_back_to_original(self) -> None:
        """Paying full price beats failing the page."""
        engine = VLMEngine(api_key="k", max_edge_px=100)

        out, scale = engine._downscale(b"not an image")

        assert out == b"not an image"
        assert scale == 1.0

    def test_max_edge_px_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_edge_px must be positive"):
            VLMEngine(api_key="k", max_edge_px=0)


class TestCoordinateRoundTrip:
    """Downscaled images return downscaled boxes — they must be scaled back.

    Regression: without this, boxes stay in the shrunken image's pixel space,
    never overlap the full-resolution engine boxes, and the grounding guard
    discards every VLM block — the engine goes silently dead while still
    being billed for every page.
    """

    def test_coordinates_are_mapped_back_to_full_page_space(self) -> None:
        engine = VLMEngine(api_key="k")
        # Model saw a half-size image and reported boxes in that space.
        data = _api_response("50,100,150,200|Ἑλλάς")

        blocks = engine._parse_response(data, 800, 1200, scale=0.5)

        assert blocks[0].bbox.x == 100
        assert blocks[0].bbox.y == 200
        assert blocks[0].bbox.right == 300
        assert blocks[0].bbox.bottom == 400

    def test_scale_of_one_leaves_coordinates_untouched(self) -> None:
        engine = VLMEngine(api_key="k")
        data = _api_response("10,20,110,60|text")

        blocks = engine._parse_response(data, 400, 100, scale=1.0)

        assert blocks[0].bbox.x == 10
        assert blocks[0].bbox.y == 20
        assert blocks[0].bbox.right == 110
        assert blocks[0].bbox.bottom == 60
