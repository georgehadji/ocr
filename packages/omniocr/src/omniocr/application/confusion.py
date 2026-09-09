"""OCR-plausible confusion candidates for unknown tokens (ENHANCEMENT_PLAN A8b).

A lexicon miss currently produces a bare ``unknown`` flag: the reviewer is
handed a problem with no proposal. This module turns the miss into at most
three candidate readings, each one a token some lexicon actually contains.

**Suggest-only.** Nothing here rewrites text. Candidates travel as
``Suggestion`` values and a human decides — CLAUDE.md rule 1.

**Substitutions are restricted to what an OCR system plausibly confuses**,
rather than to arbitrary edit distance. Generic edit-distance-1 over a large
lexicon returns dozens of unrelated words for any short token; restricting the
alphabet is what makes a top-3 worth reading. The classes are the failure
modes this project has observed or that the Greek script makes inevitable:

* **Iotacism** — ει/ι/η/υ/οι collapsed to one sound, so training data conflates
  them. The largest class in Greek OCR.
* **Sigma** — medial, final, and lunate forms of one letter.
* **Latin homoglyph** — a live instance is documented in ``reconcile.py``:
  ``biblionet.gr`` came back as ``biblionetgrt?ocfposo?ocf〉o812oceobf``.
* **Glyph shape** — ν/υ, θ/ϑ, γ/ν are close in printed faces at scan
  resolution.

**No regex.** The table is a plain mapping and generation is a bounded loop, so
a noise-page token cannot drive combinatorial expansion. Two caps enforce it:
tokens longer than ``MAX_TOKEN_LENGTH`` are refused outright, and generation
stops at ``MAX_CANDIDATES`` before ranking.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from omniocr.ports.interfaces import ILexicon

# A token longer than this is not a word. Noise pages produce 200-character
# "tokens" of joined punctuation, and every confusable character multiplies the
# candidate space, so this is a guard against combinatorial expansion rather
# than a claim about Greek vocabulary — the longest bundled forms are ~15.
MAX_TOKEN_LENGTH = 64

# Hard ceiling on generated candidates before ranking. Reached only by tokens
# made almost entirely of confusable characters.
MAX_CANDIDATES = 512

# How many reach the reviewer. Three, because a reviewer shown ten candidates
# stops reading them — the list must be scannable, not a second task.
MAX_SUGGESTIONS = 3

SUGGESTION_REASON = "confusion_candidate"


def _table() -> dict[str, tuple[str, ...]]:
    """Build the substitution table, symmetric by construction.

    Written as class groups and closed symmetrically here rather than as hand
    -maintained pairs, which invites the asymmetry bug where ``ε`` proposes
    ``αι`` but ``αι`` never proposes ``ε``.
    """
    groups: tuple[tuple[str, ...], ...] = (
        # Iotacism: historically distinct spellings of one modern sound.
        ("ει", "ι", "η", "υ", "οι"),
        ("ε", "αι"),
        ("ο", "ω"),
        # Sigma: medial, final, lunate.
        ("σ", "ς", "ϲ"),
        # Glyph shape confusions in printed faces.
        ("ν", "υ"),
        ("θ", "ϑ"),
        ("γ", "ν"),
        ("κ", "ϰ"),
        ("π", "ϖ"),
        ("φ", "ϕ"),
        ("ρ", "ϱ"),
        # Latin homoglyphs: mixed-script pages, and Greek-English models whose
        # codec carries both alphabets.
        ("ο", "o"),
        ("ν", "v"),
        ("ρ", "p"),
        ("υ", "u"),
        ("χ", "x"),
        ("τ", "t"),
        ("Α", "A"),
        ("Β", "B"),
        ("Ε", "E"),
        ("Η", "H"),
        ("Ι", "I"),
        ("Κ", "K"),
        ("Μ", "M"),
        ("Ν", "N"),
        ("Ο", "O"),
        ("Ρ", "P"),
        ("Τ", "T"),
        ("Χ", "X"),
        ("Υ", "Y"),
        ("Ζ", "Z"),
    )
    table: dict[str, set[str]] = {}
    for group in groups:
        for member in group:
            table.setdefault(member, set()).update(other for other in group if other != member)
    return {key: tuple(sorted(value)) for key, value in table.items()}


CONFUSIONS: Mapping[str, tuple[str, ...]] = _table()

# Deliberately absent: a breathing/accent class.
#
# The obvious next class is "a mark dropped, added, or swapped", since those
# are the commonest polytonic OCR errors. It is not here because it could
# never fire. `SetLexicon.contains` falls back to a monotonized comparison, so
# a token that has lost or gained an accent is *already* recognized — checked
# 2026-09-09: `contains("θεοτοκος")` is True against a lexicon holding only
# `θεοτόκος`. Such a token is therefore never flagged unknown, `suggest`
# returns early, and any accent variants generated for it would be discarded
# unread.
#
# Accent problems are handled elsewhere and better: `SuggestOnlyCorrector`
# checks for impossible combinations directly, which catches the cases that
# matter without needing a lexicon to confirm them.
#
# Restore this class only alongside a lexicon that matches diacritics
# strictly; against a lenient one it is dead code.


def _substitution_candidates(token: str) -> set[str]:
    """Every single-substitution variant reachable through CONFUSIONS.

    Substitutions may change length (``ει`` for ``ι``), so this works over
    slices rather than character positions.
    """
    generated: set[str] = set()
    for source, targets in CONFUSIONS.items():
        start = token.find(source)
        while start != -1:
            for target in targets:
                generated.add(token[:start] + target + token[start + len(source) :])
                if len(generated) >= MAX_CANDIDATES:
                    return generated
            start = token.find(source, start + 1)
    return generated


def candidates(token: str) -> tuple[str, ...]:
    """Every OCR-plausible single-edit variant of ``token``. Pure.

    Sorted, so the result is a deterministic function of its input — ranking
    must not depend on set iteration order.
    """
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return ()
    generated = _substitution_candidates(token)
    generated.discard(token)
    return tuple(sorted(generated))


def rank(
    token: str,
    generated: Iterable[str],
    lexicons: Sequence[ILexicon],
    limit: int = MAX_SUGGESTIONS,
) -> tuple[str, ...]:
    """Keep candidates a lexicon recognizes, ordered by lexicon precedence.

    ``lexicons`` is ordered most-authoritative first, and that order carries a
    real decision: the curated Byzantine and Pontian vocabularies sit **above**
    any bulk list, so a candidate found in a dialect lexicon outranks one found
    only in a generic Greek word list. Without that ordering a bulk lexicon
    would propose standard-Greek "corrections" for legitimate regional forms —
    exactly what ``lexicons.py`` exists to prevent.

    Ties inside a layer break alphabetically, so output is deterministic rather
    than dependent on generation order.
    """
    if limit <= 0:
        return ()
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for layer, lexicon in enumerate(lexicons):
        for candidate in generated:
            if candidate in seen or candidate == token:
                continue
            if lexicon.contains(candidate):
                ranked.append((layer, candidate))
                seen.add(candidate)
    ranked.sort()
    return tuple(candidate for _, candidate in ranked[:limit])


def suggest(token: str, lexicons: Sequence[ILexicon]) -> tuple[str, ...]:
    """Top candidate readings for an unknown token, best first.

    Empty when the token is already known to a lexicon: a word that checks out
    is not a finding, and flagging it would bury the real ones.
    """
    if not token:
        return ()
    if any(lexicon.contains(token) for lexicon in lexicons):
        return ()
    return rank(token, candidates(token), lexicons)


__all__ = [
    "CONFUSIONS",
    "MAX_CANDIDATES",
    "MAX_SUGGESTIONS",
    "MAX_TOKEN_LENGTH",
    "SUGGESTION_REASON",
    "candidates",
    "rank",
    "suggest",
]
