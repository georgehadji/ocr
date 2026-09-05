"""Per-document model selection (ENHANCEMENT_PLAN A6).

The three bundled Kraken models span **0.038 to 0.264 CER on the same page** —
a 7x spread — and selection is a fixed manifest default. A book set in a Porson
face silently gets a model measured on Didot. After A2 there will be a dozen
fine-tuned models and the problem gets worse, not better: more models means
more ways to pick wrong.

The scoring function is pure and testable without Kraken. The probe that feeds
it is imperative and lives in `BakeOffSelector`.

**Scoring without ground truth.** At run time there is no reference text, so
the score is a proxy built from three signals a wrong-typeface model degrades
on together:

    score = 0.5 * mean_confidence
          + 0.4 * lexicon_hit_rate
          + 0.1 * (1 - diacritic_violation_rate)

Confidence is weighted highest but not trusted alone: `ScriptAwareReconciler`
records a Kraken model reporting high confidence while emitting Latin mush.
The lexicon term is what catches that, because mush does not appear in a Greek
lexicon. The diacritic term is small because it only fires on *impossible*
combinations — two breathings on one vowel — which is a narrow signal, but a
model reading the wrong typeface produces them and a right one does not.

**These weights are shipped, not measured.** ENHANCEMENT_PLAN A6 says to
re-tune them against the A1 corpus once A8's real lexicon lands, and to record
the tuned values in an ADR. Against the current ~50-word bundled lexicons the
hit rate is close to noise and the score collapses toward confidence alone.
Treat a bake-off result as better than a blind default, not as a measurement.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

from omniocr.application.post_correction import _lexicon_key
from omniocr.domain.models import OCRLine, Script
from omniocr.ports.interfaces import ILexicon

CONFIDENCE_WEIGHT = 0.5
LEXICON_WEIGHT = 0.4
DIACRITIC_WEIGHT = 0.1

# Confidence is reported on 0-100 by some engines and 0-1 by others; the
# domain's Confidence permits either. Normalize before weighting so a model
# reporting 0.9 does not lose to one reporting 90 for the same certainty.
_CONFIDENCE_CEILING = 100.0

# Same marks SuggestOnlyCorrector checks. Spelled with escapes rather than
# literal combining characters, which are invisible in a source file and
# silently corrupted by an editor that normalizes on save.
_BREATHING_MARKS = frozenset(["\u0313", "\u0314"])  # smooth, rough
_ACCENT_MARKS = frozenset(["\u0300", "\u0301", "\u0342"])  # grave, acute, perispomeni


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One model's score on the probe sample, with its terms kept separable.

    The terms are retained rather than collapsed into the total because the
    selection has to be auditable from logs alone (A6 DoD) — "model X won"
    is not reviewable, "model X won on lexicon hit rate while losing on
    confidence" is.
    """

    model_name: str
    score: float
    mean_confidence: float
    lexicon_hit_rate: float
    diacritic_violation_rate: float
    line_count: int


def _normalized_confidence(lines: Sequence[OCRLine]) -> float:
    if not lines:
        return 0.0
    values = [line.confidence.value for line in lines]
    scale = _CONFIDENCE_CEILING if max(values) > 1.0 else 1.0
    return sum(value / scale for value in values) / len(values)


def _lexicon_hit_rate(lines: Sequence[OCRLine], lexicon: ILexicon | None) -> float:
    """Fraction of tokens the lexicon recognizes.

    Returns 0.0 with no lexicon, which makes the term inert rather than
    falsely favourable — an unknown-vocabulary model should not score as if
    every word checked out.
    """
    if lexicon is None:
        return 0.0
    tokens = [key for line in lines for token in line.text.split() if (key := _lexicon_key(token))]
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if lexicon.contains(token)) / len(tokens)


def _diacritic_violation_rate(lines: Sequence[OCRLine]) -> float:
    """Fraction of base characters carrying an impossible diacritic combination.

    Only genuinely impossible combinations count — two breathings, or two
    accents, on one vowel. An unusual but legal combination is not a defect,
    and treating it as one would penalise a model for reading polytonic
    correctly.
    """
    bases = 0
    violations = 0
    for line in lines:
        nfd = unicodedata.normalize("NFD", line.text)
        index = 0
        while index < len(nfd):
            if unicodedata.category(nfd[index]) in ("Mn", "Mc"):
                index += 1
                continue
            bases += 1
            index += 1
            breathings = accents = 0
            while index < len(nfd) and unicodedata.category(nfd[index]) in ("Mn", "Mc"):
                mark = nfd[index]
                if mark in _BREATHING_MARKS:
                    breathings += 1
                elif mark in _ACCENT_MARKS:
                    accents += 1
                index += 1
            if breathings > 1 or accents > 1:
                violations += 1
    if bases == 0:
        return 0.0
    return violations / bases


def score_candidate(
    model_name: str,
    lines: Sequence[OCRLine],
    lexicon: ILexicon | None = None,
) -> CandidateScore:
    """Score one model's reading of the probe sample. Pure.

    A model that produced no lines scores 0.0 rather than being excluded here,
    so a caller comparing candidates sees it lost rather than seeing it vanish.
    """
    confidence = _normalized_confidence(lines)
    hit_rate = _lexicon_hit_rate(lines, lexicon)
    violation_rate = _diacritic_violation_rate(lines)
    total = (
        CONFIDENCE_WEIGHT * confidence
        + LEXICON_WEIGHT * hit_rate
        + DIACRITIC_WEIGHT * (1.0 - violation_rate)
    )
    return CandidateScore(
        model_name=model_name,
        score=0.0 if not lines else total,
        mean_confidence=confidence,
        lexicon_hit_rate=hit_rate,
        diacritic_violation_rate=violation_rate,
        line_count=len(lines),
    )


def select_best(
    scores: Sequence[CandidateScore],
    default_model: str | None = None,
) -> CandidateScore | None:
    """Pick the winner, breaking ties toward the manifest default.

    Ties are not hypothetical: with no lexicon loaded and identical
    confidences, two models score identically. Falling back to the declared
    default keeps the choice deterministic and keeps `models/manifest.json`
    meaningful rather than decorative.
    """
    if not scores:
        return None
    best = max(score.score for score in scores)
    winners = [score for score in scores if score.score == best]
    if len(winners) > 1 and default_model is not None:
        for winner in winners:
            if winner.model_name == default_model:
                return winner
    return sorted(winners, key=lambda score: score.model_name)[0]


def lexicon_for(script: Script, lexicons: Mapping[Script, ILexicon] | None) -> ILexicon | None:
    if lexicons is None:
        return None
    return lexicons.get(script)


@dataclass(frozen=True, slots=True)
class BakeOffResult:
    """The winner plus every runner-up, so the choice can be reviewed."""

    winner: str
    scores: tuple[CandidateScore, ...]

    def summary(self) -> str:
        """One line per candidate, best first — what gets logged."""
        ranked = sorted(self.scores, key=lambda score: score.score, reverse=True)
        return "; ".join(
            f"{score.model_name}={score.score:.3f}"
            f"(conf={score.mean_confidence:.2f},lex={score.lexicon_hit_rate:.2f},"
            f"dia={score.diacritic_violation_rate:.2f})"
            for score in ranked
        )


class ManifestDefaultSelector:
    """Use whatever `models/manifest.json` declares. The existing behaviour."""

    def __init__(self, default_model: str) -> None:
        self._default = default_model

    def select(self, probe: object = None) -> str:
        return self._default


class BakeOffSelector:
    """Run every candidate model over a sample and keep the best scorer.

    The probe is injected rather than constructed here: this class must stay
    testable on a machine with no Kraken, and the application layer must not
    reach for infrastructure (CLAUDE.md rule 4). `probe(model_name)` returns
    the lines that model produced, or an empty sequence if it failed.

    A model that raises or times out scores zero and loses — it does not
    abort the bake-off. One broken model must not cost the document its
    other candidates.
    """

    def __init__(
        self,
        candidates: Sequence[str],
        default_model: str | None = None,
        lexicon: ILexicon | None = None,
    ) -> None:
        if not candidates:
            raise ValueError("a bake-off needs at least one candidate model")
        self._candidates = tuple(candidates)
        self._default = default_model
        self._lexicon = lexicon

    def run(self, probe: "ProbeFn") -> BakeOffResult:
        scores: list[CandidateScore] = []
        for model_name in self._candidates:
            try:
                lines = probe(model_name)
            except Exception:  # noqa: BLE001 - a failed candidate loses, it does not abort
                lines = ()
            scores.append(score_candidate(model_name, lines, self._lexicon))
        winner = select_best(scores, self._default)
        # `select_best` only returns None for an empty list, which the
        # constructor already rejects.
        assert winner is not None
        return BakeOffResult(winner=winner.model_name, scores=tuple(scores))


if TYPE_CHECKING:
    from typing import Callable

    ProbeFn = Callable[[str], Sequence[OCRLine]]


__all__ = [
    "CONFIDENCE_WEIGHT",
    "DIACRITIC_WEIGHT",
    "LEXICON_WEIGHT",
    "BakeOffResult",
    "BakeOffSelector",
    "CandidateScore",
    "ManifestDefaultSelector",
    "lexicon_for",
    "score_candidate",
    "select_best",
]
