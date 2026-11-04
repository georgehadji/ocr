"""Tests for UnlimitedOCREngine — Unlimited-OCR-2 output parsed to line blocks.

The fixture is an excerpt of the model's real output on
``polytonic-scan-1.png`` (from the 2026-11-03 accuracy spike), so the parser
is tested against the format the model actually emits, typos included.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from omniocr.composition.desktop import create_ensemble_pipeline
from omniocr.domain.models import Script, TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.resilience import CircuitBreakerEngine
from omniocr.infrastructure.unlimited_ocr import DEFAULT_MAX_EDGE_PX, UnlimitedOCREngine
from omniocr.interfaces import cli

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


@pytest.mark.parametrize(
    "data",
    [_response("plain text, no blocks"), _response(None), {}],
    ids=["no-blocks", "null-content", "no-choices"],
)
def test_unparseable_output_is_an_engine_failure(data: object) -> None:
    """A wrong prompt or chat reply must surface as Err, not as a blank page.

    No full-page fallback either: one box over every word would mis-assign
    them all.
    """
    with pytest.raises(ValueError):
        _engine()._parse_response(data, 100, 100)


def test_empty_page_is_not_a_failure() -> None:
    assert _engine()._parse_response(_response("<PAGE>"), 100, 100) == []


def test_extract_turns_unparseable_output_into_err(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    monkeypatch.setattr(engine, "_call_api", lambda _: _response("Sorry, I can't help."))

    class _Page:
        number = 1
        content = b""
        width = 100
        height = 100

    assert isinstance(engine.extract(_Page(), TenantContext("t", "u", "desktop")), Err)


def test_editorial_angle_brackets_are_kept() -> None:
    """Critical editions print supplements as ``<καὶ>``; they are text, not markup."""
    raw = "<PAGE><BLOCK>text[0, 0, 1000, 100]<FORK>τοῦ <καὶ> λόγου<|im_end|></PAGE>"
    [block] = _engine()._parse_response(_response(raw), 1000, 1000)
    assert block.text == "τοῦ <καὶ> λόγου"


def test_truncated_output_drops_the_cut_off_block() -> None:
    data = {"choices": [{"message": {"content": RAW}, "finish_reason": "length"}]}
    blocks = _engine()._parse_response(data, 2000, 3000)
    assert blocks[-1].text == "κούσματα."


def test_text_is_nfc_normalized() -> None:
    decomposed = unicodedata.normalize("NFD", "ἢ")
    raw = f"<BLOCK>text[0, 0, 1000, 100]<FORK>{decomposed}"
    [block] = _engine()._parse_response(_response(raw), 1000, 1000)
    assert block.text == "ἢ"


def test_hyphenated_labels_and_float_coords_parse() -> None:
    raw = "<BLOCK>section-header[100.4, 0, 500.6, 100]<FORK>ΚΕΦΑΛΑΙΟΝ"
    [block] = _engine()._parse_response(_response(raw), 1000, 1000)
    assert (block.text, block.bbox.x, block.bbox.w) == ("ΚΕΦΑΛΑΙΟΝ", 100, 401)


def test_inverted_and_out_of_range_boxes_are_ordered_and_clamped() -> None:
    raw = "<BLOCK>text[819, 139, 127, 1004]<FORK>α"
    [block] = _engine()._parse_response(_response(raw), 1000, 1000)
    assert (block.bbox.x, block.bbox.y, block.bbox.right, block.bbox.bottom) == (
        127,
        139,
        819,
        1000,
    )


def test_pages_are_sent_at_ingest_resolution() -> None:
    assert _engine()._max_edge_px == DEFAULT_MAX_EDGE_PX == 3508


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


def test_ensemble_guards_unlimited_ocr_with_a_breaker_not_retries() -> None:
    pipeline = create_ensemble_pipeline(
        tesseract_language="grc",
        kraken_model_path="x.mlmodel",
        unlimited_ocr_url="http://localhost:30000/v1",
    )
    by_script = pipeline._router._by_script  # type: ignore[attr-defined]
    [engine] = [e for e in by_script[Script.POLYTONIC] if e.name == "unlimited_ocr"]
    assert isinstance(engine, CircuitBreakerEngine)
    assert isinstance(engine.engine, UnlimitedOCREngine)


def test_cli_rejects_unlimited_ocr_with_tesseract(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(b"")
    code = cli.main(
        ["run", str(source), "--engine", "tesseract", "--unlimited-ocr-url", "http://x/v1"]
    )
    assert code == cli.EXIT_USAGE
