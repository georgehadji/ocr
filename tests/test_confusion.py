"""Tests for OCR-plausible confusion candidates (ENHANCEMENT_PLAN A8b).

The module is pure, so these test behaviour directly rather than plumbing.

Two properties carry most of the weight. Generation must stay **bounded** —
a noise-page token of joined punctuation must not drive combinatorial
expansion — and ranking must respect **lexicon precedence**, because a bulk
Greek word list placed above the curated dialect lexicons would propose
standard-Greek "corrections" for legitimate Pontian and Byzantine forms, which
is the failure `lexicons.py` exists to prevent.
"""

from __future__ import annotations

import pytest

from omniocr.application.confusion import (
    CONFUSIONS,
    MAX_CANDIDATES,
    MAX_SUGGESTIONS,
    MAX_TOKEN_LENGTH,
    candidates,
    rank,
    suggest,
)
from omniocr.ports.lexicon import SetLexicon

GREEK = SetLexicon(name="greek", words=["πόλις", "εἶναι", "σχηματίζεται", "καί"])


class TestTable:
    def test_every_confusion_is_symmetric(self) -> None:
        """Written as class groups precisely so this cannot drift.

        Hand-maintained pairs invite the asymmetry bug where one direction is
        proposed and the reverse never is.
        """
        for source, targets in CONFUSIONS.items():
            for target in targets:
                assert source in CONFUSIONS.get(target, ()), (
                    f"{source!r} proposes {target!r} but not the reverse"
                )

    def test_nothing_maps_to_itself(self) -> None:
        for source, targets in CONFUSIONS.items():
            assert source not in targets


class TestGeneration:
    def test_iotacism_is_reachable(self) -> None:
        """The largest error class in Greek OCR."""
        assert "πόλις" in candidates("πόλεις")

    def test_latin_homoglyph_is_reachable(self) -> None:
        """A live instance is documented in reconcile.py."""
        assert "πολις" in candidates("πoλις")  # Latin o -> Greek omicron

    def test_final_sigma_is_reachable(self) -> None:
        assert "πόλις" in candidates("πόλισ")

    def test_a_token_never_proposes_itself(self) -> None:
        assert "πόλις" not in candidates("πόλις")

    def test_generation_is_deterministic(self) -> None:
        """Ranking must not depend on set iteration order."""
        assert candidates("σχηματίζεται") == candidates("σχηματίζεται")
        assert list(candidates("πόλις")) == sorted(candidates("πόλις"))


class TestBounds:
    def test_an_over_long_token_is_refused(self) -> None:
        """Noise pages produce 200-character 'tokens' of joined punctuation."""
        assert candidates("α" * (MAX_TOKEN_LENGTH + 1)) == ()

    def test_a_token_at_the_limit_is_allowed(self) -> None:
        # "ο" is confusable (omicron/omega/Latin o); "α" is not in the table at
        # all, so an all-alpha token legitimately yields nothing.
        assert candidates("ο" * MAX_TOKEN_LENGTH) != ()

    def test_generation_stays_bounded_for_a_dense_token(self) -> None:
        """Every character confusable — the worst case for expansion."""
        dense = "ο" * MAX_TOKEN_LENGTH
        assert len(candidates(dense)) <= MAX_CANDIDATES

    def test_empty_token_yields_nothing(self) -> None:
        assert candidates("") == ()


class TestRanking:
    def test_only_candidates_the_lexicon_knows_survive(self) -> None:
        proposals = suggest("πόλεις", [GREEK])
        assert proposals == ("πόλις",)

    def test_a_known_token_produces_no_proposals(self) -> None:
        """A word that checks out is not a finding; flagging it buries real ones."""
        assert suggest("πόλις", [GREEK]) == ()

    def test_an_unrecognizable_token_produces_no_proposals(self) -> None:
        """Better to admit none than to invent one."""
        assert suggest("ζζζζζ", [GREEK]) == ()

    def test_output_is_capped(self) -> None:
        assert len(suggest("πόλεις", [GREEK])) <= MAX_SUGGESTIONS

    def test_zero_limit_returns_nothing(self) -> None:
        assert rank("πόλεις", candidates("πόλεις"), [GREEK], limit=0) == ()


class TestLexiconPrecedence:
    """The dialect lexicons must outrank a bulk list.

    This is the whole reason `rank` takes an ordered sequence. A generic Greek
    word list placed first would propose standard-Greek readings for
    legitimate Pontian and Byzantine forms — the exact behaviour
    `lexicons.py`'s docstring says it exists to prevent.
    """

    def test_a_dialect_candidate_outranks_a_bulk_one(self) -> None:
        # Both readings must be reachable in ONE edit from the token, or the
        # unreachable one never matches and the test proves nothing: "κουτάλια"
        # needs two edits from "κουτάλην" and silently made this vacuous.
        dialect = SetLexicon(name="pontian", words=["κουτάλιν"])
        bulk = SetLexicon(name="bulk", words=["κουτάλειν"])

        proposals = rank("κουτάλην", candidates("κουτάλην"), [dialect, bulk])

        assert proposals, "neither lexicon matched — the fixture is wrong, not the code"
        assert proposals[0] == "κουτάλιν"

    def test_reversing_precedence_reverses_the_proposal(self) -> None:
        """Guards against the ordering being accidentally ignored."""
        dialect = SetLexicon(name="pontian", words=["κουτάλιν"])
        bulk = SetLexicon(name="bulk", words=["κουτάλειν"])

        first = rank("κουτάλην", candidates("κουτάλην"), [dialect, bulk])
        second = rank("κουτάλην", candidates("κουτάλην"), [bulk, dialect])

        assert first[0] != second[0]

    def test_a_candidate_is_not_proposed_twice(self) -> None:
        shared = SetLexicon(name="a", words=["πόλις"])
        duplicate = SetLexicon(name="b", words=["πόλις"])

        assert rank("πόλεις", candidates("πόλεις"), [shared, duplicate]) == ("πόλις",)


class TestPostCorrectorIntegration:
    def test_a_lexicon_miss_carries_a_proposal_not_just_a_flag(self) -> None:
        """The point of A8b: hand the reviewer a proposal, not a problem."""
        from omniocr.application.post_correction import SuggestOnlyCorrector
        from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext

        corrector = SuggestOnlyCorrector(lexicons={Script.POLYTONIC: GREEK})
        line = OCRLine(
            id="l1",
            text="πόλεις",
            confidence=Confidence(0.9),
            bbox=BBox(0, 0, 10, 10),
            script=Script.POLYTONIC,
        )
        context = TenantContext(organization_id="o", user_id="u", subscription_tier="desktop")

        result = corrector.correct(line, context)
        assert result.is_ok()
        proposals = [s for s in result.value if s.reason == "confusion_candidate"]

        assert proposals, "a lexicon miss produced no candidate reading"
        assert proposals[0].suggestion_text == "πόλις"
        assert proposals[0].source_text == "πόλεις"

    def test_the_recognized_text_is_never_rewritten(self) -> None:
        """CLAUDE.md rule 1: suggestions only, the human decides."""
        from omniocr.application.post_correction import SuggestOnlyCorrector
        from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext

        corrector = SuggestOnlyCorrector(lexicons={Script.POLYTONIC: GREEK})
        line = OCRLine(
            id="l1",
            text="πόλεις",
            confidence=Confidence(0.9),
            bbox=BBox(0, 0, 10, 10),
            script=Script.POLYTONIC,
        )
        context = TenantContext(organization_id="o", user_id="u", subscription_tier="desktop")

        corrector.correct(line, context)

        assert line.text == "πόλεις"

    def test_an_unrecognizable_token_still_gets_the_bare_flag(self) -> None:
        """Falling silent would be worse: the reviewer loses the finding."""
        from omniocr.application.post_correction import SuggestOnlyCorrector
        from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext

        corrector = SuggestOnlyCorrector(lexicons={Script.POLYTONIC: GREEK})
        line = OCRLine(
            id="l1",
            text="ζζζζζ",
            confidence=Confidence(0.9),
            bbox=BBox(0, 0, 10, 10),
            script=Script.POLYTONIC,
        )
        context = TenantContext(organization_id="o", user_id="u", subscription_tier="desktop")

        result = corrector.correct(line, context)
        assert result.is_ok()
        reasons = {s.reason for s in result.value}

        assert "not_in_greek_lexicon" in reasons


@pytest.mark.parametrize("token", ["", "α", "πόλις", "σχηματίζεται", "ο" * 64])
def test_generation_never_raises(token: str) -> None:
    candidates(token)
