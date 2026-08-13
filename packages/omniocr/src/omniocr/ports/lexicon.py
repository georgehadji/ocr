from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from omniocr.ports.interfaces import ILexicon


def _key(token: str) -> str:
    return unicodedata.normalize("NFC", token).lower()


# Breathing marks, accents, and iota subscript — the diacritics monotonic
# Greek drops and OCR is most likely to lose or hallucinate. Diaeresis
# (U+0308) is deliberately excluded: it is a phonemic marker monotonic
# Greek keeps, not accent noise, so stripping it would risk false matches.
_ACCENT_MARKS: frozenset[str] = frozenset(
    [
        "̀",  # grave
        "́",  # acute
        "͂",  # circumflex / perispomeni
        "̓",  # smooth breathing
        "̔",  # rough breathing
        "ͅ",  # iota subscript
    ]
)


def _monotonize(key: str) -> str:
    decomposed = unicodedata.normalize("NFD", key)
    stripped = "".join(ch for ch in decomposed if ch not in _ACCENT_MARKS)
    return unicodedata.normalize("NFC", stripped)


class SetLexicon(ILexicon):
    """Small in-memory lexicon adapter suitable for tests and bundled vocabularies.

    Both the stored words and the queried token are NFC-normalized and
    lowercased. Greek has too many ways to spell the same word for exact byte
    matching to be meaningful: ``εὐχή`` composed and decomposed are different
    strings, and a word list contributed as NFD would silently match nothing
    at all. Normalizing here means a caller cannot forget to.

    This is Unicode hygiene, not an orthographic change (CLAUDE.md rule 5) —
    the lexicon only answers yes/no and never rewrites text.

    A miss on the exact (NFC, lowercased) key falls back to a monotonized
    comparison before answering "unknown". A polytonic scan that drops one
    breathing mark or accent — an OCR mark error, not a different word — must
    not read as "not a word at all"; those are different failure classes and
    the diacritic checks in ``SuggestOnlyCorrector`` already flag the former.
    Conflating them under one lexicon miss is what used to flood every page
    with false ``not_in_<x>_lexicon`` suggestions.
    """

    def __init__(self, name: str, words: Sequence[str]) -> None:
        self.name = name
        self._words = frozenset(_key(word) for word in words)
        self._monotonized = frozenset(_monotonize(word) for word in self._words)

    def contains(self, token: str) -> bool:
        key = _key(token)
        if key in self._words:
            return True
        return _monotonize(key) in self._monotonized


__all__ = ["SetLexicon"]
