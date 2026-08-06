from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from omniocr.ports.interfaces import ILexicon


def _key(token: str) -> str:
    return unicodedata.normalize("NFC", token).lower()


class SetLexicon(ILexicon):
    """Small in-memory lexicon adapter suitable for tests and bundled vocabularies.

    Both the stored words and the queried token are NFC-normalized and
    lowercased. Greek has too many ways to spell the same word for exact byte
    matching to be meaningful: ``εὐχή`` composed and decomposed are different
    strings, and a word list contributed as NFD would silently match nothing
    at all. Normalizing here means a caller cannot forget to.

    This is Unicode hygiene, not an orthographic change (CLAUDE.md rule 5) —
    the lexicon only answers yes/no and never rewrites text.
    """

    def __init__(self, name: str, words: Sequence[str]) -> None:
        self.name = name
        self._words = frozenset(_key(word) for word in words)

    def contains(self, token: str) -> bool:
        return _key(token) in self._words


__all__ = ["SetLexicon"]
