"""Typed hyphen-join verdicts and suggestion kinds (state-machine plan, phase 5).

The enum values are the strings the code already produced, so nothing that
reads a ``LineJoin`` or a ``Suggestion`` changes; these tests pin the new
vocabulary and the invariants it adds.
"""

from __future__ import annotations

import pytest

from omniocr.domain.models import (
    JoinSeparator,
    JoinVerdict,
    LineJoin,
    ParagraphRole,
    Suggestion,
    SuggestionKind,
    apply_suggestions,
)


def _join(separator: JoinSeparator, removed: str, verdict: JoinVerdict) -> LineJoin:
    return LineJoin("a", "b", separator, removed, verdict)


def test_the_enum_values_are_the_strings_already_in_use() -> None:
    assert [v.value for v in JoinVerdict] == [
        "joined_in_lexicon",
        "hyphen_in_lexicon",
        "unverified",
    ]
    assert [s.value for s in JoinSeparator] == ["", "-"]
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
    ],
)
def test_an_incoherent_join_is_rejected(
    separator: JoinSeparator, removed: str, verdict: JoinVerdict
) -> None:
    with pytest.raises(ValueError, match="illegal join"):
        _join(separator, removed, verdict)


def test_a_plain_wrap_is_not_a_join_so_a_space_separator_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid"):
        LineJoin("a", "b", " ", "", "unverified")  # type: ignore[arg-type]


def test_plain_strings_are_coerced_to_their_members() -> None:
    join = LineJoin("a", "b", "", "-", "unverified")  # type: ignore[arg-type]

    assert join.separator is JoinSeparator.DROPPED
    assert join.verdict is JoinVerdict.UNVERIFIED


def test_paragraph_roles_format_as_their_values() -> None:
    assert f"{ParagraphRole.BODY}" == "body"
    assert ParagraphRole.BODY == "body"


def test_a_suggestion_with_half_a_span_is_a_whole_line_for_every_applier() -> None:
    half = _suggestion(start=0)
    whole = _suggestion(source_text="abc", suggestion_text="Z")

    assert half.kind is SuggestionKind.WHOLE_LINE
    # Whole-line suggestions are applied first, in order; spans right to left
    # after. A half span is not a span, so it is applied with the whole lines
    # (here "b" and then "Z"), not after them.
    assert apply_suggestions("abc", [half, whole]) == "Z"


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
