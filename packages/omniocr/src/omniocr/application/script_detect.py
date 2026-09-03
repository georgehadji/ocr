"""Evidence that a recognized line is Latin script, not Greek.

Used to stop a Greek-only recognizer's output from winning a line that is
actually English, German, or French. Detection runs on *recognized text*
rather than the line image: the layout stage stamps one script on every line
(``KrakenLayoutAnalyzer``), so the script is not actually known until an
engine has read the line.

The whole difficulty is homoglyphs. Greek capitals ``ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ``
share glyph shapes with Latin ``TA BYZANTINA MNHMEIA``, so a Latin-configured
Tesseract reads Greek titles as confident Latin. Counting "Latin letters"
naively therefore misclassifies Greek majuscule headings — the exact text a
book of this kind puts in its titles. Only letters with no Greek lookalike
count as evidence.
"""

from __future__ import annotations

# Latin letters that a Greek capital or lowercase form can be mistaken for.
# Ambiguous both ways, so they are evidence of nothing.
_HOMOGLYPHS: frozenset[str] = frozenset(
    # Greek capitals Α Β Ε Ζ Η Ι Κ Μ Ν Ο Ρ Τ Υ Χ read as these Latin capitals
    "ABEZHIKMNOPTYX"
    # Greek lowercase ο ν ρ χ α κ τ υ γ ι read as these Latin lowercase
    "onpxaktuyiv"
)

# A short URL fragment ("gr", "de") can be genuinely Latin while scoring low,
# so the threshold stays small; three unambiguous letters is already more than
# homoglyph noise produces on Greek text.
MIN_LATIN_EVIDENCE = 3


def latin_evidence(text: str) -> int:
    """Count Latin letters in ``text`` that no Greek letter resembles.

    Greek majuscule headings score 0 here by construction, which is the point.
    """
    return sum(
        1
        for character in text
        if character.isascii() and character.isalpha() and character not in _HOMOGLYPHS
    )


def greek_letter_count(text: str) -> int:
    """Count characters in the Greek and Greek Extended blocks."""
    return sum(1 for character in text if "Ͱ" <= character <= "Ͽ" or "ἀ" <= character <= "῿")


def is_latin_dominant(text: str, threshold: int = MIN_LATIN_EVIDENCE) -> bool:
    """True when Latin evidence both clears ``threshold`` and outweighs Greek.

    The absolute count alone is not enough. ``yedos τῆς ὑδρίας δείχνει`` — a
    Greek line whose first word was misread into Latin — scores 3 on
    ``latin_evidence`` (e, d, s) and would otherwise be judged a Latin line on
    the strength of one corrupted word, handing the whole line to the engine
    that corrupted it. Requiring Latin to outweigh Greek on the same line
    keeps the decision proportional.

    Consequence worth knowing: a *mostly Greek* line with a genuine Latin
    fragment inside it (a French book title cited mid-sentence) is judged
    Greek, so the fragment is not recovered. Fixing that properly needs
    word-level routing, not line-level.
    """
    return latin_evidence(text) >= threshold and latin_evidence(text) >= greek_letter_count(text)


def ascii_letter_count(text: str) -> int:
    """Count ASCII letters, homoglyphs included.

    On a line with no genuine Latin evidence, ASCII letters are the signature
    of Greek majuscule misread into lookalikes — ``ΕΡΓΑ ΤΟΥ ΙδΙΟΥ`` coming
    back as ``ΕΡΓΑ TOY IAIOY``. Measured on the Δεδούσης scans: a Tesseract
    with Latin packs loaded wins such lines on confidence and Latinizes the
    book's own headings.
    """
    return sum(1 for character in text if character.isascii() and character.isalpha())


__all__ = [
    "MIN_LATIN_EVIDENCE",
    "ascii_letter_count",
    "is_latin_dominant",
    "latin_evidence",
]
