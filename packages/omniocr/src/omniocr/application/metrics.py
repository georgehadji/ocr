from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar


Token = TypeVar("Token")


@dataclass(frozen=True, slots=True)
class RegressionBaseline:
    """Reviewed CER/WER values for one engine and script fixture."""

    cer: float
    wer: float

    def __post_init__(self) -> None:
        if self.cer < 0 or self.wer < 0:
            raise ValueError("regression metrics must not be negative")


def _edit_distance(reference: Sequence[Token], hypothesis: Sequence[Token]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_character in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_character in enumerate(hypothesis, start=1):
            substitution = previous[hypothesis_index - 1] + (
                reference_character != hypothesis_character
            )
            insertion = current[hypothesis_index - 1] + 1
            deletion = previous[hypothesis_index] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Return normalized NFC character error rate in the inclusive range [0, 1+]."""
    normalized_reference = unicodedata.normalize("NFC", reference)
    normalized_hypothesis = unicodedata.normalize("NFC", hypothesis)
    if not normalized_reference:
        return 0.0 if not normalized_hypothesis else 1.0
    return _edit_distance(normalized_reference, normalized_hypothesis) / len(normalized_reference)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return normalized whitespace-token error rate in the inclusive range [0, 1+]."""
    reference_words = tuple(unicodedata.normalize("NFC", reference).split())
    hypothesis_words = tuple(unicodedata.normalize("NFC", hypothesis).split())
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return _edit_distance(reference_words, hypothesis_words) / len(reference_words)


def regression_exceeded(
    reference: str,
    hypothesis: str,
    baseline: RegressionBaseline,
    tolerance: float = 0.0,
) -> bool:
    """Return whether either error rate exceeds its reviewed baseline."""
    if tolerance < 0:
        raise ValueError("tolerance must not be negative")
    return (
        character_error_rate(reference, hypothesis) > baseline.cer + tolerance
        or word_error_rate(reference, hypothesis) > baseline.wer + tolerance
    )


__all__ = [
    "RegressionBaseline",
    "character_error_rate",
    "regression_exceeded",
    "word_error_rate",
]
