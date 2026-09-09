from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from pathlib import Path

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


class SortedBlobLexicon(ILexicon):
    """A large lexicon read from a sorted blob by binary search.

    Backs the bulk word lists (ENHANCEMENT_PLAN A8a): roughly 2.5M forms
    across three files. A ``frozenset`` of that many Greek strings costs
    ~250 MB resident and buys nothing over ``bisect`` on an mmap, which is
    O(log n) with near-zero resident cost because the pages stay in the OS
    file cache and are shared between processes.

    The file must be sorted **by UTF-8 byte order**, which is what
    ``build_lexicon.py`` writes: comparison happens on encoded bytes, so
    sorting by Python string order and searching by byte order would silently
    miss entries above U+007F — which is every Greek word.

    Matching mirrors ``SetLexicon`` exactly, including the monotonized
    fallback, so swapping one for the other cannot change which tokens are
    flagged. That parity is asserted in the tests.
    """

    __slots__ = ("name", "_path", "_data")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self._path = path
        # Read once into bytes. mmap was the plan's suggestion, but on Windows
        # it holds a file handle for the process lifetime, which breaks any
        # later rebuild of the blob; the OS page cache already makes a plain
        # read cheap and the bytes are shared copy-on-write after a fork.
        self._data = path.read_bytes()

    def _line_bounds(self, position: int) -> tuple[int, int]:
        """Start and end of the line containing ``position``."""
        start = self._data.rfind(b"\n", 0, position) + 1
        end = self._data.find(b"\n", start)
        return start, len(self._data) if end == -1 else end

    def _holds(self, key: str) -> bool:
        """Binary search the blob directly, without an offset index.

        An index of line offsets was the obvious first implementation and the
        wrong one: 890k Python ints per lexicon cost ~30 MB resident and five
        seconds to build, which is most of what avoiding a frozenset was meant
        to save. Bisecting on byte positions and snapping to line boundaries
        needs no index at all.

        Comparison is on **UTF-8 bytes**, matching the byte-order sort that
        ``build_lexicon.py`` writes. Sorting one way and searching the other
        would silently miss every entry above U+007F — which is every Greek
        word, and would look like a merely incomplete lexicon rather than a
        broken one.
        """
        needle = key.encode("utf-8")
        low, high = 0, len(self._data)
        while low < high:
            middle = (low + high) // 2
            start, end = self._line_bounds(middle)
            if start < low:
                # `middle` landed inside the line before the window; take the
                # next line so the window always shrinks and the loop ends.
                start = low
                end = self._data.find(b"\n", start)
                if end == -1:
                    end = len(self._data)
            if self._data[start:end] < needle:
                low = end + 1
            else:
                high = start
        if low >= len(self._data):
            return False
        start, end = self._line_bounds(low)
        return self._data[start:end] == needle

    def contains(self, token: str) -> bool:
        """Match the exact form, then the accent-stripped one.

        The blob stores both spellings (``build_lexicon.py`` writes the
        monotonized variant alongside each form), which is what lets a
        monotonic word list serve polytonic pages. An exact-only lexicon was
        measured on 2026-09-09 flagging 309 of 456 tokens on a
        human-transcribed page: the target books are modern Greek in
        polytonic orthography, so every accented word missed a monotonic list.

        Behaviour therefore matches ``SetLexicon`` exactly, which the tests
        assert — the leniency simply lives in the data rather than in a
        second in-memory index.
        """
        key = _key(token)
        return self._holds(key) or self._holds(_monotonize(key))

    def __len__(self) -> int:
        """Number of forms. Counts newlines rather than caching an index."""
        return self._data.count(b"\n")


class LayeredLexicon(ILexicon):
    """Several lexicons consulted in order, most authoritative first.

    ``contains`` is the union — a form in any layer is known. The ordering
    exists for ``confusion.rank``, which proposes candidates from earlier
    layers first: the curated Byzantine and Pontian vocabularies sit above the
    bulk lists so a dialect reading outranks a generic one. Without that a
    bulk lexicon would propose standard-Greek "corrections" for legitimate
    regional forms, which is what ``lexicons.py`` exists to prevent.
    """

    __slots__ = ("name", "layers")

    def __init__(self, name: str, layers: Sequence[ILexicon]) -> None:
        if not layers:
            raise ValueError("a layered lexicon needs at least one layer")
        self.name = name
        self.layers = tuple(layers)

    def contains(self, token: str) -> bool:
        return any(layer.contains(token) for layer in self.layers)
