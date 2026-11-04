"""Suggestions apply to the right text (audit PR3)."""

from __future__ import annotations

from omniocr.application.alignment import MAX_TOKENS, merge_suggestion
from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRLine,
    Script,
    Suggestion,
    TenantContext,
    apply_suggestion,
    apply_suggestions,
)
from omniocr.ports.lexicon import SetLexicon

CTX = TenantContext("org", "user", "desktop")


def _line(text: str, engine: str | None = None) -> OCRLine:
    provenance = None
    if engine is not None:
        provenance = EngineRun(
            engine=engine,
            model_ref=ModelRef(engine=engine, model_name=engine, model_hash="h"),
            model_hash="h",
            params=(),
            timestamp="2026-11-04T00:00:00Z",
        )
    return OCRLine(
        "l1",
        text,
        Confidence(90),
        BBox(0, 0, 100, 10),
        script=Script.POLYTONIC,
        provenance=provenance,
    )


def _correct(text: str, words: tuple[str, ...]) -> list[Suggestion]:
    corrector = SuggestOnlyCorrector({Script.POLYTONIC: SetLexicon("greek", words)})
    return list(corrector.correct(_line(text), CTX).value)


# --- [5] spans and one apply function -------------------------------------


def test_a_token_suggestion_keeps_the_rest_of_the_line() -> None:
    text = "Ὁ θεὸς λέγει,"
    found = [s for s in _correct(text, ("λέγη",)) if s.reason == "confusion_candidate"]

    assert [s.suggestion_text for s in found] == ["λέγη,"]
    assert apply_suggestion(text, found[0]) == "Ὁ θεὸς λέγη,"


def test_the_span_is_where_the_token_sits_in_the_line() -> None:
    text = "Ὁ θεὸς λέγει,"
    suggestion = next(s for s in _correct(text, ("λέγη",)) if s.reversible and s.start is not None)

    assert text[suggestion.start : suggestion.end] == suggestion.source_text == "λέγει,"


def test_two_accepted_suggestions_on_one_line_both_apply() -> None:
    first = Suggestion("l", "αα", "βββ", "x", start=0, end=2)
    second = Suggestion("l", "γγ", "δ", "x", start=3, end=5)
    text = "αα γγ"

    assert apply_suggestions(text, [first, second]) == "βββ δ"
    # One at a time, in click order: the first changed the length under the second.
    assert apply_suggestion(apply_suggestion(text, first), second) == "βββ δ"


def test_a_flag_cannot_be_accepted() -> None:
    flags = [s for s in _correct("ἄγνωστον", ("λόγος",)) if not s.reversible]

    assert flags
    assert all(apply_suggestion("ἄγνωστον", s) == "ἄγνωστον" for s in flags)


def test_diacritic_flags_do_not_carry_a_replacement() -> None:
    text = "ἀ̔ β"  # two breathings on one letter
    flags = [s for s in _correct(text, ()) if s.reason == "multiple_breathing_marks"]

    assert flags and all(not s.reversible and s.suggestion_text == "" for s in flags)


def test_a_span_that_no_longer_matches_is_not_guessed() -> None:
    ambiguous = Suggestion("l", "αα", "β", "x", start=0, end=2)

    assert apply_suggestion("γγ αα αα", ambiguous) == "γγ αα αα"
    assert apply_suggestion("γγ αα", ambiguous) == "γγ β"  # one occurrence: unambiguous


def test_a_whole_line_suggestion_still_replaces_the_line() -> None:
    assert apply_suggestion("old", Suggestion("l", "old", "new", "x")) == "new"


# --- [S1] long lines ------------------------------------------------------


def test_a_line_over_the_token_cap_gets_no_merge() -> None:
    long_text = " ".join(f"w{i}" for i in range(MAX_TOKENS + 90))
    candidates = [_line(long_text, "a"), _line(long_text, "b")]

    assert merge_suggestion(candidates[0], candidates) is None


# --- [S2] capital homoglyphs ----------------------------------------------


def test_a_latin_capital_inside_a_greek_word_is_proposed_back_as_greek() -> None:
    suggestions = _correct("ΚAΙ", ("καί",))  # the A is Latin

    proposals = [s for s in suggestions if s.reason == "confusion_candidate"]
    assert [s.suggestion_text for s in proposals] == ["ΚΑΙ"]
    assert apply_suggestion("ΚAΙ", proposals[0]) == "ΚΑΙ"


# --- [S3] abbreviations on whole tokens -----------------------------------


def _expansions(text: str) -> list[str]:
    found = SuggestOnlyCorrector().correct(_line(text), CTX).value
    return [s.suggestion_text for s in found if s.reason == "reversible_abbreviation_expansion"]


def test_an_abbreviation_inside_a_word_is_not_expanded() -> None:
    assert _expansions("εἴδε") == []
    assert _expansions("ἀναστ. ἀνα-στ") == []


def test_a_whole_token_abbreviation_still_expands() -> None:
    assert _expansions("ἄνθρωπος κ.τ.λ.") == ["ἄνθρωπος καὶ τὰ λοιπά"]
    assert _expansions("βλ. σελ.") == ["βλέπε σελίς"]
