"""Lexicon lookup: the checks that made every word miss.

The lexicon is suggest-only, so a broken lookup does not corrupt text — it
floods the reviewer with false "unknown word" flags on every token of every
line, which is the fastest way to make a review UI useless.
"""

from __future__ import annotations

import unicodedata

import pytest

from omniocr.application.post_correction import SuggestOnlyCorrector, _lexicon_key
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext
from omniocr.domain.result import Ok
from omniocr.infrastructure.lexicons import byzantine_lexicon, lexicons_by_script
from omniocr.ports.lexicon import SetLexicon

_CONTEXT = TenantContext(organization_id="t", user_id="u", subscription_tier="desktop")


def _line(text: str, script: Script = Script.BYZANTINE) -> OCRLine:
    return OCRLine(
        id="l1",
        text=text,
        confidence=Confidence(95.0),
        bbox=BBox(0, 0, 10, 10),
        script=script,
    )


def _lexicon_reasons(line: OCRLine) -> list[str]:
    corrector = SuggestOnlyCorrector(lexicons=lexicons_by_script())
    result = corrector.correct(line, _CONTEXT)
    assert isinstance(result, Ok)
    return [s.source_text for s in result.value if s.reason.startswith("not_in_")]


class TestSetLexiconNormalizes:
    def test_decomposed_token_matches_composed_entry(self) -> None:
        """``εὐαγγέλιον`` in NFD is a different string from the NFC entry.

        Nothing guarantees engine output is NFC — the corrector raises its own
        ``unicode_nfc`` suggestion precisely because it often is not.
        """
        lexicon = byzantine_lexicon()
        word = "εὐαγγέλιον"

        assert lexicon.contains(unicodedata.normalize("NFC", word))
        assert lexicon.contains(unicodedata.normalize("NFD", word))

    def test_decomposed_source_word_is_still_findable(self) -> None:
        """A word list contributed as NFD must not silently match nothing."""
        lexicon = SetLexicon(name="t", words=[unicodedata.normalize("NFD", "εὐχή")])

        assert lexicon.contains("εὐχή")

    def test_lookup_is_case_insensitive(self) -> None:
        assert byzantine_lexicon().contains("ΛΕΙΤΟΥΡΓΊΑ") is True

    def test_unknown_word_is_still_unknown(self) -> None:
        """Normalizing must not turn the lexicon into something that says yes."""
        assert byzantine_lexicon().contains("σιδηρόδρομος") is False


class TestCorrectorLookup:
    def test_known_word_is_not_flagged(self) -> None:
        assert _lexicon_reasons(_line("θεοτόκος")) == []

    def test_decomposed_known_word_is_not_flagged(self) -> None:
        """The live defect: the lookup used raw ``line.text``, so an NFD page
        flagged every single token."""
        assert _lexicon_reasons(_line(unicodedata.normalize("NFD", "θεοτόκος"))) == []

    @pytest.mark.parametrize("punctuated", ["θεοτόκος,", "θεοτόκος.", "θεοτόκος·", "«θεοτόκος»"])
    def test_trailing_punctuation_does_not_make_a_word_unknown(self, punctuated: str) -> None:
        """``str.split()`` breaks on whitespace only, so the comma came along."""
        assert _lexicon_reasons(_line(punctuated)) == []

    def test_genuinely_unknown_word_is_still_flagged(self) -> None:
        """The guard on all of the above: the check must still do its job."""
        assert _lexicon_reasons(_line("σιδηρόδρομος")) == ["σιδηρόδρομος"]

    def test_flag_reports_the_original_token_not_the_lookup_key(self) -> None:
        """Faithfulness (CLAUDE.md rule 1): only the lookup is normalized, and
        the reviewer sees exactly what was on the page."""
        assert _lexicon_reasons(_line("Σιδηρόδρομος,")) == ["Σιδηρόδρομος,"]


class TestLexiconKey:
    def test_bare_punctuation_reduces_to_empty_and_is_skipped(self) -> None:
        """A stray em dash must not be reported as a word missing from the lexicon."""
        assert _lexicon_key("—") == ""
        assert _lexicon_reasons(_line("θεοτόκος —")) == []
