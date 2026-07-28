"""Tests for CalamariEngine — optional subprocess adapter with GPL isolation.

These tests only exercise the _parse_output method since actual Calamari
predictions require the calamari-ocr package on PATH.
"""

from __future__ import annotations

from omniocr.infrastructure.calamari import CalamariEngine


def test_calamari_parse_output_empty() -> None:
    """Empty or malformed output produces no blocks."""
    engine = CalamariEngine()

    assert engine._parse_output("", 100, 100) == []
    assert engine._parse_output("invalid json", 100, 100) == []


def test_calamari_parse_output_extracts_texts() -> None:
    """Typical Calamari JSON output is parsed into OCRBlocks."""
    engine = CalamariEngine()
    output = '{"texts": ["hello", "world"]}'
    blocks = engine._parse_output(output, 200, 100)

    assert len(blocks) == 2
    assert blocks[0].text == "hello"
    assert blocks[1].text == "world"
    assert blocks[1].id == "calamari-0-1"


def test_calamari_extract_returns_install_hint_when_not_on_path() -> None:
    """When calamari-predict is not installed, extract returns a helpful Err."""

    engine = CalamariEngine()

    class _NullPage:
        number = 1
        content = b"fake"
        width = 100
        height = 100

    result = engine.extract(
        _NullPage(),
        type(
            "ctx",
            (),
            {
                "organization_id": "o",
                "user_id": "u",
                "subscription_tier": "d",
                "custom_model_id": None,
            },
        )(),
    )

    assert result.is_err()
    assert "calamari-predict not found on PATH" in str(result.error)
    assert "pip install" in str(result.error)


def test_calamari_parse_output_handles_list_of_predictions() -> None:
    """Calamari may return a list of prediction dicts."""
    engine = CalamariEngine()
    output = '[{"texts": ["first"]}, {"texts": ["second"]}]'
    blocks = engine._parse_output(output, 200, 100)

    assert len(blocks) == 2
    assert blocks[0].text == "first"
    assert blocks[1].text == "second"


def test_calamari_parse_output_skips_empty_texts() -> None:
    """Empty text entries in the predictions list are skipped."""
    engine = CalamariEngine()
    output = '{"texts": ["valid", "", "also valid"]}'
    blocks = engine._parse_output(output, 200, 100)

    assert len(blocks) == 2
    assert blocks[0].text == "valid"
    assert blocks[1].text == "also valid"
