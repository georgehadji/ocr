from __future__ import annotations

import re
from typing import Mapping

from omniocr.domain.models import LineJoin, OCRLine, Script
from omniocr.ports.interfaces import ILexicon

HYPHENS = ("-", "‐", "¬", "\u00ad")


def extract_join_candidates(first_text: str, second_text: str) -> tuple[str, str, str] | None:
    """Extract word A and word B from two consecutive lines if the first ends with a hyphen.

    Returns (A, B, exact_hyphen_char) or None.
    """
    first_stripped = first_text.rstrip()
    if not first_stripped:
        return None
    last_char = first_stripped[-1]
    if last_char not in HYPHENS:
        return None

    # Split first line by spaces to find the last word
    first_words = first_stripped.split()
    if not first_words:
        return None
    last_word = first_words[-1]
    if len(last_word) < 2 or last_word[-1] != last_char:
        return None
    A = last_word[:-1]

    # Split second line by spaces to find the first word
    second_words = second_text.strip().split()
    if not second_words:
        return None
    B = second_words[0]

    # Strip any trailing punctuation from B to get a clean word
    B_clean = re.sub(r"[^\w]+$", "", B)
    if not B_clean:
        return None

    return A, B_clean, last_char


class Dehyphenator:
    """Policy object that decides whether to join two consecutive lines across a hyphen."""

    def __init__(self, lexicons: Mapping[Script, ILexicon]) -> None:
        self.lexicons = lexicons

    def join(self, first: OCRLine, second: OCRLine) -> LineJoin | None:
        extracted = extract_join_candidates(first.text, second.text)
        if not extracted:
            return None
        A, B, hyphen_char = extracted

        lexicon = self.lexicons.get(first.script)

        ab_word = A + B
        ab_hyphenated = A + "-" + B

        ab_in_lexicon = lexicon.contains(ab_word) if lexicon is not None else False
        ab_hyphenated_in_lexicon = (
            lexicon.contains(ab_hyphenated) if lexicon is not None else False
        )

        if ab_in_lexicon and not ab_hyphenated_in_lexicon:
            # joined_in_lexicon -> drop hyphen
            return LineJoin(
                first_line_id=first.id,
                second_line_id=second.id,
                separator="",
                removed=hyphen_char,
                verdict="joined_in_lexicon",
            )
        elif ab_hyphenated_in_lexicon:
            # hyphen_in_lexicon -> keep hyphen
            return LineJoin(
                first_line_id=first.id,
                second_line_id=second.id,
                separator="-",
                removed="",
                verdict="hyphen_in_lexicon",
            )
        else:
            # unverified -> drop hyphen (default is drop hyphen, separator = "")
            return LineJoin(
                first_line_id=first.id,
                second_line_id=second.id,
                separator="",
                removed=hyphen_char,
                verdict="unverified",
            )
