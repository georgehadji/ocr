"""Tests for per-document model selection scoring (ENHANCEMENT_PLAN A6).

`score_candidate` is pure, so it is tested directly without Kraken — which
matters, because the whole point of A6 is to be exercised on machines that
cannot run a bake-off.

The monotonicity tests are the load-bearing ones. A scoring function that is
not monotone in its own terms can rank a model higher for reading *worse*, and
with three terms combined by weights that is not obvious by inspection.
"""

from __future__ import annotations

import pytest

from omniocr.application.model_selection import (
    CONFIDENCE_WEIGHT,
    DIACRITIC_WEIGHT,
    LEXICON_WEIGHT,
    BakeOffSelector,
    ManifestDefaultSelector,
    score_candidate,
    select_best,
)
from omniocr.domain.models import BBox, Confidence, OCRLine
from omniocr.ports.lexicon import SetLexicon


def _line(text: str, confidence: float = 0.9) -> OCRLine:
    return OCRLine(
        id="line-1",
        text=text,
        confidence=Confidence(confidence),
        bbox=BBox(0, 0, 100, 10),
    )


GREEK_LEXICON = SetLexicon(name="test", words=["πόλις", "τῶν", "ἡ"])


class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        """Otherwise the score is not on a 0-1 scale and thresholds mislead."""
        assert CONFIDENCE_WEIGHT + LEXICON_WEIGHT + DIACRITIC_WEIGHT == pytest.approx(1.0)


class TestMonotonicity:
    def test_higher_confidence_never_scores_lower(self) -> None:
        low = score_candidate("m", [_line("ἡ πόλις", confidence=0.4)])
        high = score_candidate("m", [_line("ἡ πόλις", confidence=0.95)])
        assert high.score > low.score

    def test_more_lexicon_hits_never_score_lower(self) -> None:
        miss = score_candidate("m", [_line("ξξξ ζζζ")], GREEK_LEXICON)
        hit = score_candidate("m", [_line("ἡ πόλις")], GREEK_LEXICON)
        assert hit.score > miss.score
        assert hit.lexicon_hit_rate == pytest.approx(1.0)
        assert miss.lexicon_hit_rate == pytest.approx(0.0)

    def test_diacritic_violations_never_score_higher(self) -> None:
        clean = score_candidate("m", [_line("ἡ")])
        # Two breathings on one vowel — impossible in polytonic orthography.
        broken = score_candidate("m", [_line("ἀ̔")])
        assert clean.score > broken.score
        assert broken.diacritic_violation_rate > 0.0


class TestConfidenceScales:
    def test_percent_and_fraction_scales_agree(self) -> None:
        """Engines disagree on whether confidence is 0-1 or 0-100.

        Without normalization a model reporting 0.9 loses to one reporting 90
        for identical certainty — a scale artefact deciding model selection.
        """
        fraction = score_candidate("m", [_line("ἡ πόλις", confidence=0.9)])
        percent = score_candidate("m", [_line("ἡ πόλις", confidence=90.0)])
        assert fraction.mean_confidence == pytest.approx(percent.mean_confidence)


class TestDegenerateInput:
    def test_a_model_that_produced_nothing_scores_zero(self) -> None:
        """It must lose visibly rather than vanish from the comparison."""
        empty = score_candidate("m", [])
        assert empty.score == 0.0
        assert empty.line_count == 0

    def test_no_lexicon_makes_the_term_inert_not_favourable(self) -> None:
        """Absent vocabulary must not score as if every word checked out."""
        scored = score_candidate("m", [_line("ξξξ ζζζ")], lexicon=None)
        assert scored.lexicon_hit_rate == 0.0

    def test_text_without_letters_has_no_violation_rate(self) -> None:
        assert score_candidate("m", [_line("   ")]).diacritic_violation_rate == 0.0


class TestSelectBest:
    def test_highest_score_wins(self) -> None:
        scores = [
            score_candidate("worse", [_line("ξξξ", confidence=0.3)], GREEK_LEXICON),
            score_candidate("better", [_line("ἡ πόλις", confidence=0.95)], GREEK_LEXICON),
        ]
        winner = select_best(scores)
        assert winner is not None
        assert winner.model_name == "better"

    def test_ties_fall_back_to_the_manifest_default(self) -> None:
        """Ties are not hypothetical: no lexicon plus equal confidence ties exactly.

        Falling back to the declared default keeps selection deterministic and
        keeps models/manifest.json meaningful rather than decorative.
        """
        scores = [
            score_candidate("alpha", [_line("ἡ πόλις")]),
            score_candidate("declared-default", [_line("ἡ πόλις")]),
        ]
        winner = select_best(scores, default_model="declared-default")
        assert winner is not None
        assert winner.model_name == "declared-default"

    def test_ties_without_a_default_are_still_deterministic(self) -> None:
        scores = [
            score_candidate("zeta", [_line("ἡ πόλις")]),
            score_candidate("alpha", [_line("ἡ πόλις")]),
        ]
        assert select_best(list(scores)) == select_best(list(reversed(scores)))

    def test_no_candidates_selects_nothing(self) -> None:
        assert select_best([]) is None


class TestBakeOff:
    def test_the_best_reader_wins(self) -> None:
        def probe(model: str) -> list[OCRLine]:
            if model == "right-typeface":
                return [_line("ἡ πόλις τῶν", confidence=0.95)]
            return [_line("ξξξ ζζζ ααα", confidence=0.4)]

        selector = BakeOffSelector(["wrong-typeface", "right-typeface"], lexicon=GREEK_LEXICON)
        assert selector.run(probe).winner == "right-typeface"

    def test_a_failing_model_loses_without_aborting_the_bake_off(self) -> None:
        """One broken candidate must not cost the document its other candidates."""

        def probe(model: str) -> list[OCRLine]:
            if model == "broken":
                raise RuntimeError("kraken exploded")
            return [_line("ἡ πόλις", confidence=0.9)]

        result = BakeOffSelector(["broken", "working"], lexicon=GREEK_LEXICON).run(probe)

        assert result.winner == "working"
        broken = next(score for score in result.scores if score.model_name == "broken")
        assert broken.score == 0.0
        assert broken.line_count == 0

    def test_a_model_returning_nothing_loses_visibly(self) -> None:
        """Scored and beaten, not silently dropped from the comparison."""

        def probe(model: str) -> list[OCRLine]:
            return [] if model == "silent" else [_line("ἡ πόλις")]

        result = BakeOffSelector(["silent", "working"]).run(probe)

        assert result.winner == "working"
        assert {score.model_name for score in result.scores} == {"silent", "working"}

    def test_every_candidate_is_reported_not_just_the_winner(self) -> None:
        """A6 DoD: the choice must be auditable from logs alone."""
        result = BakeOffSelector(["a", "b", "c"]).run(lambda model: [_line("ἡ πόλις")])
        summary = result.summary()

        assert len(result.scores) == 3
        for name in ("a", "b", "c"):
            assert name in summary
        assert "conf=" in summary and "lex=" in summary and "dia=" in summary

    def test_an_empty_candidate_list_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            BakeOffSelector([])


class TestManifestDefaultSelector:
    def test_returns_the_declared_default(self) -> None:
        assert ManifestDefaultSelector("declared").select() == "declared"


class TestAuditability:
    def test_the_score_keeps_its_terms_separable(self) -> None:
        """A6 DoD: the choice must be auditable from logs alone.

        "model X won" is not reviewable; "won on lexicon hit rate while losing
        on confidence" is.
        """
        scored = score_candidate("m", [_line("ἡ πόλις", confidence=0.8)], GREEK_LEXICON)
        assert scored.mean_confidence == pytest.approx(0.8)
        assert scored.lexicon_hit_rate == pytest.approx(1.0)
        assert scored.diacritic_violation_rate == pytest.approx(0.0)
        assert scored.score == pytest.approx(
            CONFIDENCE_WEIGHT * 0.8 + LEXICON_WEIGHT * 1.0 + DIACRITIC_WEIGHT * 1.0
        )
