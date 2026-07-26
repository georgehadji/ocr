from __future__ import annotations

from omniocr.application.metrics import (
    RegressionBaseline,
    character_error_rate,
    regression_exceeded,
    word_error_rate,
)


def test_character_error_rate_normalizes_unicode_before_scoring() -> None:
    assert character_error_rate("ά", "α\u0301") == 0.0


def test_character_and_word_error_rate_report_insertions_and_substitutions() -> None:
    assert character_error_rate("abc", "adc") == 1 / 3
    assert word_error_rate("one two", "one three") == 1 / 2


def test_error_rate_handles_empty_reference() -> None:
    assert character_error_rate("", "extra") == 1.0
    assert word_error_rate("", "extra words") == 1.0


def test_regression_gate_applies_cer_and_wer_tolerance() -> None:
    baseline = RegressionBaseline(cer=0.2, wer=0.5)

    assert not regression_exceeded("one two", "one too", baseline)
    assert regression_exceeded("one two", "three four", baseline)
    assert not regression_exceeded("one two", "three four", baseline, tolerance=1.0)
