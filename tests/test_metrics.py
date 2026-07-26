from __future__ import annotations

from omniocr.application.metrics import character_error_rate, word_error_rate


def test_character_error_rate_normalizes_unicode_before_scoring() -> None:
    assert character_error_rate("ά", "α\u0301") == 0.0


def test_character_and_word_error_rate_report_insertions_and_substitutions() -> None:
    assert character_error_rate("abc", "adc") == 1 / 3
    assert word_error_rate("one two", "one three") == 1 / 2


def test_error_rate_handles_empty_reference() -> None:
    assert character_error_rate("", "extra") == 1.0
    assert word_error_rate("", "extra words") == 1.0
