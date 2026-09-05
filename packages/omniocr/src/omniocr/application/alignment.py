"""Word-level alignment and voting across engine candidates (ENHANCEMENT_PLAN A4).

Both existing reconcilers pick one whole line and discard the rest. With A3's
preprocessing variants and A7's extra engines there will be ten or more
candidates per line, and throwing away all but one is indefensible: a line
where Kraken reads the Greek correctly and Tesseract reads the Latin footnote
marker correctly currently loses half its correct output either way.

This module is pure — a deterministic transform over token sequences, no I/O
and no state. Everything here is a function of its arguments.

**Faithfulness (CLAUDE.md rule 1).** A merged line is a synthesis no engine
produced, so nothing here may become `OCRLine.text`. The merge is emitted as a
`Suggestion` carrying per-token provenance; a human decides. `AlignedReconciler`
still returns a real engine's real reading.

Two deliberate ceilings, both stated rather than hidden:

*Pivot alignment, not multiple-sequence alignment.* Every candidate is aligned
pairwise against the highest-confidence candidate. True MSA is exponential;
this is O(k·n²) and the pivot is the reading most likely to be right.

*Only substitutions vote.* Where candidates insert or delete tokens relative to
the pivot, the pivot's reading stands. Voting on insertions would let a
majority of engines add a token the pivot never saw, which is a synthesis too
far for a stage whose whole purpose is to stay traceable.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Mapping, Sequence

from omniocr.domain.models import AgreementTier, AlignedToken, OCRLine, Suggestion

# Lines longer than this are not prose. SequenceMatcher is O(n²) in the worst
# case, and a segmenter that merged a whole page into one "line" would other-
# wise turn a per-line merge into a per-page quadratic blow-up.
MAX_TOKENS = 512

MERGE_REASON = "alignment_merge"


def _engine_name(line: OCRLine) -> str:
    """The voter behind a candidate: engine, plus preprocessing variant if any.

    The variant belongs in the key. With A3's variant ensembling one engine
    produces several candidates from differently binarized images, and keying
    on the engine alone would collapse them into a single voter — the ensemble
    benefit would silently disappear, and a reviewer could not tell which
    image a reading came from.
    """
    provenance = line.provenance
    if provenance is None:
        return "unknown"
    name = provenance.model_ref.model_name
    variant = getattr(provenance, "variant", "")
    return f"{name}@{variant}" if variant else name


def _weight(engine: str, weights: Mapping[str, float] | None) -> float:
    """Vote weight for one engine.

    Keyed by engine name rather than by candidate, so the pivot's own engine
    is weighted like any other. Keying it off the non-pivot candidates meant a
    weight given for the pivot's engine was silently ignored.

    Defaults to 1.0 for every engine — deliberately, not as a placeholder to
    be tuned by feel. ENHANCEMENT_PLAN A4 calls for weights measured per
    variety on the A1 dev split, and that split does not exist yet. An
    unweighted majority is the honest default until it does; guessed weights
    would be indistinguishable from measured ones in the code and impossible
    to audit later.

    Self-reported engine confidence is deliberately *not* used as a weight:
    `ScriptAwareReconciler`'s docstring records a Kraken model reporting high
    confidence while emitting Latin mush on a URL.
    """
    if weights is None:
        return 1.0
    return weights.get(engine, 1.0)


def align(
    pivot: OCRLine,
    others: Sequence[OCRLine],
    weights: Mapping[str, float] | None = None,
) -> tuple[AlignedToken, ...]:
    """Align every candidate against the pivot and vote position by position.

    Returns one `AlignedToken` per pivot token, in order. A position where
    every candidate agrees has a single reading; a contested one carries all
    readings with the engine that supplied each.
    """
    pivot_tokens = pivot.text.split()[:MAX_TOKENS]
    if not pivot_tokens:
        return ()

    pivot_engine = _engine_name(pivot)
    # Position -> engine -> that engine's reading of this position.
    readings: list[dict[str, str]] = [{pivot_engine: token} for token in pivot_tokens]

    for candidate in others:
        candidate_tokens = candidate.text.split()[:MAX_TOKENS]
        engine = _engine_name(candidate)
        matcher = SequenceMatcher(None, pivot_tokens, candidate_tokens, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    readings[i1 + offset].setdefault(engine, candidate_tokens[j1 + offset])
            elif tag == "replace" and (i2 - i1) == (j2 - j1):
                # Only a same-length substitution maps token to token. A
                # ragged replace (2 pivot tokens for 3) has no honest per-token
                # correspondence, so the pivot stands rather than guessing one.
                for offset in range(i2 - i1):
                    readings[i1 + offset].setdefault(engine, candidate_tokens[j1 + offset])
            # 'insert' and 'delete' intentionally ignored — see module docstring.

    return tuple(
        _vote(position, pivot_tokens[position], readings[position], pivot_engine, weights)
        for position in range(len(pivot_tokens))
    )


def _vote(
    position: int,
    pivot_text: str,
    position_readings: Mapping[str, str],
    pivot_engine: str,
    weights: Mapping[str, float] | None,
) -> AlignedToken:
    """Pick the winning reading for one position by weighted majority.

    Ties go to the pivot. That is not arbitrary: the pivot is the
    highest-confidence whole-line reading, and a rule that breaks ties any
    other way would make the merge depend on candidate ordering.
    """
    by_engine = {**{pivot_engine: pivot_text}, **position_readings}

    # A plain float dict rather than Counter: Counter's values are ints, and
    # vote weights are floats.
    tally: dict[str, float] = {}
    for engine, text in by_engine.items():
        tally[text] = tally.get(text, 0.0) + _weight(engine, weights)

    best = max(tally.values())
    winners = sorted(text for text, score in tally.items() if score == best)
    winner = pivot_text if pivot_text in winners else winners[0]

    return AlignedToken(
        position=position,
        pivot_text=pivot_text,
        winning_text=winner,
        readings=tuple(sorted(by_engine.items())),
    )


def agreement_tier(candidates: Sequence[OCRLine]) -> AgreementTier:
    """Classify how much the candidates agreed (ENHANCEMENT_PLAN A5).

    A pure function of the candidate texts, compared NFC-normalized with
    whitespace collapsed — two engines differing only in how they spaced a
    line did not disagree about what it says, and calling that SPLIT would
    flood the review queue with non-findings.

    This is a better confidence signal than any engine reports about itself,
    because it is evidence from independent readers rather than a model's
    opinion of its own output.
    """
    if not candidates:
        return AgreementTier.UNKNOWN
    if len(candidates) == 1:
        return AgreementTier.SINGLE

    texts = [unicodedata.normalize("NFC", " ".join(c.text.split())) for c in candidates]
    counts = Counter(texts)
    if len(counts) == 1:
        return AgreementTier.UNANIMOUS
    if max(counts.values()) >= 2:
        return AgreementTier.MAJORITY
    return AgreementTier.SPLIT


def merged_text(tokens: Sequence[AlignedToken]) -> str:
    """The voted line. Never becomes `OCRLine.text` — see the module docstring."""
    return " ".join(token.winning_text for token in tokens)


def merge_suggestion(
    chosen: OCRLine,
    candidates: Sequence[OCRLine],
    weights: Mapping[str, float] | None = None,
) -> Suggestion | None:
    """Propose the voted merge for `chosen`, or None when there is nothing to say.

    Returns None when fewer than two candidates exist, or when the vote agrees
    with the chosen reading — a suggestion identical to the text it suggests
    replacing is noise in a review queue.
    """
    if len(candidates) < 2:
        return None
    others = [candidate for candidate in candidates if candidate is not chosen]
    tokens = align(chosen, others, weights)
    if not tokens:
        return None
    merged = merged_text(tokens)
    if merged == " ".join(chosen.text.split()):
        return None
    return Suggestion(
        line_id=chosen.id,
        source_text=chosen.text,
        suggestion_text=merged,
        reason=MERGE_REASON,
    )


__all__ = [
    "MAX_TOKENS",
    "MERGE_REASON",
    "agreement_tier",
    "align",
    "merge_suggestion",
    "merged_text",
]
