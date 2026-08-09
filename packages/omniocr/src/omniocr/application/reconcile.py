from __future__ import annotations

from typing import Sequence

from omniocr.application.script_detect import (
    MIN_LATIN_EVIDENCE,
    ascii_letter_count,
    is_latin_dominant,
)
from omniocr.domain.errors import EngineError
from omniocr.domain.models import OCRLine, TenantContext
from omniocr.domain.result import Err, Ok, Result


def _best_by_confidence(candidates: Sequence[OCRLine]) -> OCRLine:
    return max(candidates, key=lambda candidate: candidate.confidence.value)


class ConfidenceWeightedReconciler:
    """Choose the highest-confidence candidate while preserving its provenance."""

    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]:
        if not candidates:
            return Err(EngineError("no OCR candidates available"))
        return Ok(_best_by_confidence(candidates))


# Tesseract language packs written in Latin script. A candidate whose
# provenance names one of these was produced by an engine that could actually
# spell a western-language line.
LATIN_LANGUAGE_PACKS: frozenset[str] = frozenset(
    {
        "eng",
        "deu",
        "deu_latf",
        "fra",
        "frm",
        "ita",
        "ita_old",
        "spa",
        "spa_old",
        "por",
        "lat",
        "nld",
        "dan",
        "swe",
        "nor",
        "fin",
        "pol",
        "ces",
        "ron",
        "hun",
        "tur",
    }
)


def _reads_latin(line: OCRLine) -> bool:
    """True when the engine behind ``line`` had a Latin-script model loaded."""
    provenance = line.provenance
    if provenance is None:
        return False
    packs = provenance.model_ref.model_name.split("+")
    return any(pack.strip() in LATIN_LANGUAGE_PACKS for pack in packs)


class ScriptAwareReconciler:
    """Confidence-weighted, except on lines only one engine was equipped to read.

    Confidence alone is the wrong signal on a mixed-script page. A Kraken model
    trained on Greek will happily emit near-Latin mush for a footnote URL and
    report high confidence doing it — observed on this project's own material,
    where ``biblionet.gr`` came back as
    ``biblionetgrt?ocfposo?ocf〉o812oceobf``. That mush contains Latin letters,
    so "which candidate looks Latin" does not separate the two; the deciding
    fact is which engine had a Latin model loaded at all.

    So a candidate wins on script grounds only when both hold: its provenance
    names a Latin language pack, and its text carries Latin evidence that
    survives homoglyph filtering. On a genuine Greek line a Latin-equipped
    Tesseract still outputs Greek, shows no Latin evidence, and gets no
    preference — and a Greek majuscule heading scores zero evidence by
    construction, so titles are safe.

    Candidates without provenance are treated as not Latin-capable, which
    degrades to plain confidence weighting rather than to a wrong guess.

    This never edits text — it only chooses among candidates the engines
    produced, so CLAUDE.md rule 1 holds.
    """

    def __init__(self, threshold: int = MIN_LATIN_EVIDENCE) -> None:
        self._threshold = threshold

    def reconcile(
        self, candidates: Sequence[OCRLine], context: TenantContext
    ) -> Result[OCRLine, EngineError]:
        if not candidates:
            return Err(EngineError("no OCR candidates available"))

        equipped = [
            candidate
            for candidate in candidates
            if _reads_latin(candidate) and is_latin_dominant(candidate.text, self._threshold)
        ]
        if equipped and len(equipped) < len(candidates):
            return Ok(_best_by_confidence(equipped))

        # No candidate is genuinely Latin. Any ASCII letters left are therefore
        # homoglyphs — Greek majuscule misread as lookalikes — so prefer the
        # reading that stayed in Greek script. Confidence cannot see this: a
        # Latin-equipped Tesseract reports high confidence on `ΕΡΓΑ TOY IAIOY`
        # and would otherwise beat Kraken's correct `ΕΡΓΑ ΤΟΥ ΙδΙΟΥ`.
        if not any(is_latin_dominant(candidate.text, self._threshold) for candidate in candidates):
            counts = [ascii_letter_count(candidate.text) for candidate in candidates]
            fewest = min(counts)
            # Only when one reading is essentially pure Greek. If every
            # candidate carries heavy ASCII the line genuinely contains Latin
            # (a URL quoted mid-sentence), and "fewest ASCII" would hand it to
            # whichever engine mangled the URL hardest.
            if fewest < self._threshold and max(counts) > fewest:
                greek_side = [
                    candidate
                    for candidate in candidates
                    if ascii_letter_count(candidate.text) == fewest
                ]
                return Ok(_best_by_confidence(greek_side))

        return Ok(_best_by_confidence(candidates))


__all__ = [
    "LATIN_LANGUAGE_PACKS",
    "ConfidenceWeightedReconciler",
    "ScriptAwareReconciler",
]
