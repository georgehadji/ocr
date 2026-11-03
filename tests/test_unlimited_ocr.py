"""Tests for UnlimitedOCREngine — Unlimited-OCR-2 output parsed to line blocks.

The fixture is an excerpt of the model's real output on
``polytonic-scan-1.png`` (from the 2026-11-03 accuracy spike), so the parser
is tested against the format the model actually emits, typos included.
"""

from __future__ import annotations

from omniocr.composition.desktop import create_ensemble_pipeline
from omniocr.infrastructure.unlimited_ocr import UnlimitedOCREngine

RAW = (
    "<PAGE><BLOCK>header[240, 52, 730, 62]<FORK>ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΕΑΛΟΝΙΚΗΣ\n"
    "<BLOCK>text[127, 107, 819, 139]<FORK>μὴν ἀντισχύξη τὴ Θεσσαλονίκη μὲ βλέμμα "
    "πρόκατείλημμένο ἀπὸ ἀ-\n"
    "κούσματα.\n"
    "<BLOCK>image[100, 600, 900, 800]<FORK>\n"
    "<BLOCK>footer[450, 908, 523, 917]<FORK>— 21 —"
)


def _response(content: object) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _engine() -> UnlimitedOCREngine:
    return UnlimitedOCREngine(api_url="http://localhost:30000/v1")


def test_each_printed_line_becomes_its_own_block() -> None:
    blocks = _engine()._parse_response(_response(RAW), 2000, 3000)

    assert [block.text for block in blocks] == [
        "ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΕΑΛΟΝΙΚΗΣ",
        "μὴν ἀντισχύξη τὴ Θεσσαλονίκη μὲ βλέμμα πρόκατείλημμένο ἀπὸ ἀ-",
        "κούσματα.",
        "— 21 —",
    ]
    assert all(block.confidence.value == 0.0 for block in blocks)
    assert blocks[0].provenance is not None
    assert blocks[0].provenance.engine == "unlimited_ocr"


def test_block_box_is_scaled_from_thousandths_and_split_into_strips() -> None:
    blocks = _engine()._parse_response(_response(RAW), 2000, 3000)
    first, second = blocks[1], blocks[2]

    # [127, 107, 819, 139] on a 2000x3000 page -> x 254..1638, y 321..417.
    assert (first.bbox.x, first.bbox.w) == (254, 1384)
    assert (second.bbox.x, second.bbox.w) == (254, 1384)
    assert first.bbox.y == 321
    assert first.bbox.bottom == second.bbox.y
    assert second.bbox.bottom == 417


def test_scale_is_ignored_because_boxes_are_normalized() -> None:
    engine = _engine()
    full = engine._parse_response(_response(RAW), 2000, 3000)
    shrunk = engine._parse_response(_response(RAW), 2000, 3000, scale=0.5)
    assert [block.bbox for block in full] == [block.bbox for block in shrunk]


def test_image_blocks_and_markup_are_dropped() -> None:
    table = "<BLOCK>table[0, 0, 1000, 100]<FORK><table><tr><td>Α</td><td>Β</td></tr></table>"
    blocks = _engine()._parse_response(_response(RAW + table), 1000, 1000)

    assert not any(block.id.startswith("unlimited_ocr-2-") for block in blocks)
    assert blocks[-1].text == "Α Β"


def test_unparseable_output_yields_no_blocks() -> None:
    """No full-page fallback: one box over every word would mis-assign them all."""
    engine = _engine()
    assert engine._parse_response(_response("plain text, no blocks"), 100, 100) == []
    assert engine._parse_response(_response(None), 100, 100) == []
    assert engine._parse_response({}, 100, 100) == []


def test_ensemble_adds_unlimited_ocr_only_when_url_given() -> None:
    def names(pipeline: object) -> set[str]:
        by_script = pipeline._router._by_script  # type: ignore[attr-defined]
        return {engine.name for engines in by_script.values() for engine in engines}

    without = create_ensemble_pipeline(tesseract_language="grc", kraken_model_path="x.mlmodel")
    with_url = create_ensemble_pipeline(
        tesseract_language="grc",
        kraken_model_path="x.mlmodel",
        unlimited_ocr_url="http://localhost:30000/v1",
    )

    assert "unlimited_ocr" not in names(without)
    assert "unlimited_ocr" in names(with_url)
