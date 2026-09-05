"""Tests for the word-level alignment merge (ENHANCEMENT_PLAN A4).

The module is pure, so these are cheap and can be exhaustive about behaviour
rather than about plumbing. The assertions that matter most are the
faithfulness ones: the merge must never invent a token no engine produced, and
it must never reach `OCRLine.text`.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from omniocr.application.alignment import MAX_TOKENS, align, merge_suggestion, merged_text
from omniocr.application.reconcile import AlignedReconciler
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRLine,
    TenantContext,
)

CONTEXT = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")


def _line(text: str, engine: str = "kraken", confidence: float = 0.9) -> OCRLine:
    return OCRLine(
        id="line-1",
        text=text,
        confidence=Confidence(confidence),
        bbox=BBox(0, 0, 100, 10),
        provenance=EngineRun(
            engine=engine,
            model_ref=ModelRef(engine=engine, model_name=engine, model_hash="hash"),
            model_hash="hash",
            params=(),
            timestamp="2026-09-05T00:00:00Z",
        ),
    )


class TestAlign:
    def test_identical_candidates_agree_everywhere(self) -> None:
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        tokens = align(pivot, [_line("ἡ πόλις τῶν Θεσσαλονικέων", engine="tesseract")])

        assert len(tokens) == 4
        assert not any(token.contested for token in tokens)
        assert merged_text(tokens) == pivot.text

    def test_one_divergence_isolates_exactly_one_position(self) -> None:
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        other = _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="tesseract")

        tokens = align(pivot, [other])
        contested = [token for token in tokens if token.contested]

        assert len(contested) == 1
        assert contested[0].position == 3
        assert contested[0].pivot_text == "Θεσσαλονικέων"

    def test_majority_overrules_the_pivot(self) -> None:
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        agree_a = _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="tesseract")
        agree_b = _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="calamari")

        tokens = align(pivot, [agree_a, agree_b])

        assert tokens[3].winning_text == "Θεσσαλονικαίων"

    def test_a_tie_goes_to_the_pivot(self) -> None:
        """Otherwise the merge would depend on which engine happened to run first."""
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        other = _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="tesseract")

        tokens = align(pivot, [other])

        assert tokens[3].winning_text == "Θεσσαλονικέων"

    def test_readings_carry_per_engine_provenance(self) -> None:
        """A reviewer must see which engine supplied a token, not just the token."""
        pivot = _line("ἡ πόλις", engine="kraken")
        other = _line("ἡ πόλεις", engine="tesseract")

        tokens = align(pivot, [other])

        assert dict(tokens[1].readings) == {"kraken": "πόλις", "tesseract": "πόλεις"}

    def test_ragged_replacement_leaves_the_pivot_alone(self) -> None:
        """Two pivot tokens against three has no honest token correspondence."""
        pivot = _line("ἡ πόλις τῶν")
        other = _line("ἡ πό λις τῶν", engine="tesseract")

        tokens = align(pivot, [other])

        assert merged_text(tokens) == "ἡ πόλις τῶν"

    def test_empty_pivot_yields_no_tokens(self) -> None:
        assert align(_line("   "), [_line("something", engine="tesseract")]) == ()

    def test_token_cap_is_enforced(self) -> None:
        long_line = " ".join(str(index) for index in range(MAX_TOKENS + 50))
        tokens = align(_line(long_line), [_line(long_line, engine="tesseract")])

        assert len(tokens) == MAX_TOKENS


class TestFaithfulness:
    def test_never_invents_a_token_no_engine_produced(self) -> None:
        """The assertion CLAUDE.md rule 1 exists for."""
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        others = [
            _line("ἡ πόλεις τῶν Θεσσαλονικαίων", engine="tesseract"),
            _line("ἠ πόλις τὸν Θεσσαλονικέων", engine="calamari"),
        ]

        tokens = align(pivot, others)
        produced = {token for line in [pivot, *others] for token in line.text.split()}

        for token in tokens:
            assert token.winning_text in produced

    def test_the_merge_is_a_suggestion_not_the_line(self) -> None:
        """`reconcile` must hand back a real engine's real reading."""
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων")
        others = [
            _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="tesseract"),
            _line("ἡ πόλις τῶν Θεσσαλονικαίων", engine="calamari"),
        ]
        reconciler = AlignedReconciler()

        chosen = reconciler.reconcile([pivot, *others], CONTEXT)
        assert chosen.is_ok()
        assert chosen.value.text in {line.text for line in [pivot, *others]}

        suggestion = reconciler.suggest(chosen.value, [pivot, *others])
        assert suggestion is not None
        assert suggestion.reason == "alignment_merge"
        assert suggestion.suggestion_text != chosen.value.text


class TestMergeSuggestion:
    def test_a_single_candidate_has_nothing_to_merge(self) -> None:
        assert merge_suggestion(_line("ἡ πόλις"), [_line("ἡ πόλις")]) is None

    def test_agreement_produces_no_suggestion(self) -> None:
        """A suggestion identical to the text it replaces is noise in a queue."""
        pivot = _line("ἡ πόλις τῶν")
        other = _line("ἡ πόλις τῶν", engine="tesseract")

        assert merge_suggestion(pivot, [pivot, other]) is None

    def test_weights_can_overrule_a_numeric_majority(self) -> None:
        pivot = _line("ἡ πόλις", engine="kraken")
        others = [
            _line("ἡ πόλεις", engine="tesseract"),
            _line("ἡ πόλεις", engine="easyocr"),
        ]

        unweighted = align(pivot, others)
        assert unweighted[1].winning_text == "πόλεις"

        weighted = align(pivot, others, weights={"kraken": 3.0})
        assert weighted[1].winning_text == "πόλις"


class TestOrderStability:
    @given(st.permutations(["alpha", "beta", "gamma", "delta"]))
    def test_result_is_independent_of_candidate_order(self, order: list[str]) -> None:
        """Given a fixed pivot, permuting the others must not change the merge.

        A vote that depends on iteration order is a vote that gives different
        answers to the same evidence.
        """
        pivot = _line("ἡ πόλις τῶν Θεσσαλονικέων", engine="pivot")
        texts = {
            "alpha": "ἡ πόλεις τῶν Θεσσαλονικέων",
            "beta": "ἡ πόλεις τῶν Θεσσαλονικαίων",
            "gamma": "ἠ πόλις τῶν Θεσσαλονικαίων",
            "delta": "ἡ πόλις τὸν Θεσσαλονικέων",
        }
        others = [_line(texts[name], engine=name) for name in order]

        assert merged_text(align(pivot, others)) == merged_text(
            align(pivot, [_line(texts[name], engine=name) for name in sorted(texts)])
        )
