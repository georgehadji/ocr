"""Typed hyphen-join verdicts and suggestion kinds (state-machine plan, phase 5).

The enum values are the strings the code already produced, so nothing that
reads a ``LineJoin`` or a ``Suggestion`` changes; these tests pin the new
vocabulary and the invariants it adds.
"""

from __future__ import annotations

import pytest

from omniocr.domain.models import JoinSeparator, JoinVerdict, LineJoin, Suggestion, SuggestionKind


def _join(separator: JoinSeparator, removed: str, verdict: JoinVerdict) -> LineJoin:
    return LineJoin("a", "b", separator, removed, verdict)


def test_the_enum_values_are_the_strings_already_in_use() -> None:
    assert [v.value for v in JoinVerdict] == [
        "joined_in_lexicon",
        "hyphen_in_lexicon",
        "unverified",
    ]
    assert [s.value for s in JoinSeparator] == ["", "-", " "]
    assert [k.value for k in SuggestionKind] == ["replace", "flag", "whole_line"]


def test_a_plain_string_still_equals_its_member() -> None:
    join = _join(JoinSeparator.DROPPED, "-", JoinVerdict.UNVERIFIED)

    assert join.separator == ""
    assert join.verdict == "unverified"


@pytest.mark.parametrize(
    ("separator", "removed", "verdict"),
    [
        (JoinSeparator.DROPPED, "-", JoinVerdict.JOINED_IN_LEXICON),
        (JoinSeparator.DROPPED, "­", JoinVerdict.UNVERIFIED),
        (JoinSeparator.KEPT, "", JoinVerdict.HYPHEN_IN_LEXICON),
    ],
)
def test_the_joins_the_dehyphenator_makes_are_legal(
    separator: JoinSeparator, removed: str, verdict: JoinVerdict
) -> None:
    assert _join(separator, removed, verdict).verdict is verdict


@pytest.mark.parametrize(
    ("separator", "removed", "verdict"),
    [
        # a hyphen kept in the lexicon cannot also have been dropped
        (JoinSeparator.DROPPED, "-", JoinVerdict.HYPHEN_IN_LEXICON),
        # a joined word keeps no hyphen
        (JoinSeparator.KEPT, "", JoinVerdict.JOINED_IN_LEXICON),
        (JoinSeparator.KEPT, "", JoinVerdict.UNVERIFIED),
        # a dropped hyphen is a character that was removed; unjoin needs it
        (JoinSeparator.DROPPED, "", JoinVerdict.JOINED_IN_LEXICON),
        # a kept hyphen removed nothing
        (JoinSeparator.KEPT, "-", JoinVerdict.HYPHEN_IN_LEXICON),
        # a plain wrap is not a hyphen decision, so no verdict goes with it
        (JoinSeparator.SPACE, "", JoinVerdict.UNVERIFIED),
    ],
)
def test_an_incoherent_join_is_rejected(
    separator: JoinSeparator, removed: str, verdict: JoinVerdict
) -> None:
    with pytest.raises(ValueError, match="illegal join"):
        _join(separator, removed, verdict)


def _suggestion(**overrides: object) -> Suggestion:
    fields: dict[str, object] = {
        "line_id": "l",
        "source_text": "a",
        "suggestion_text": "b",
        "reason": "x",
    }
    return Suggestion(**{**fields, **overrides})  # type: ignore[arg-type]


def test_a_suggestion_without_a_span_replaces_the_whole_line() -> None:
    assert _suggestion().kind is SuggestionKind.WHOLE_LINE


def test_a_suggestion_with_a_span_replaces_that_span() -> None:
    assert _suggestion(start=0, end=1).kind is SuggestionKind.REPLACE


def test_a_suggestion_that_is_not_reversible_is_a_flag_whatever_its_span() -> None:
    assert _suggestion(reversible=False).kind is SuggestionKind.FLAG
    assert _suggestion(reversible=False, start=0, end=1).kind is SuggestionKind.FLAG


def test_kind_is_derived_not_stored() -> None:
    assert "kind" not in Suggestion.__dataclass_fields__
