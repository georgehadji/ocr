from __future__ import annotations

from collections.abc import Sequence

from omniocr.ports.interfaces import ILexicon


class SetLexicon(ILexicon):
    """Small in-memory lexicon adapter suitable for tests and bundled vocabularies."""

    def __init__(self, name: str, words: Sequence[str]) -> None:
        self.name = name
        self._words = frozenset(words)

    def contains(self, token: str) -> bool:
        return token in self._words


__all__ = ["SetLexicon"]
