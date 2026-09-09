"""Post-correction: faithful (suggest-only) correctors for OCR output.

Per ARCHITECTURE.md §5 and BUILD_PLAN §4.11: post-correction is always
suggest-only — it appends ``Suggestion`` objects keyed to a line id and
never mutates the recognized source text.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    OCRLine,
    Script,
    Suggestion,
    TenantContext,
)
from omniocr.application import confusion
from omniocr.domain.result import Ok, Result
from omniocr.ports.interfaces import ILexicon, IPostCorrector


def _lexicon_key(token: str) -> str:
    """Reduce a whitespace token to the form a lexicon is keyed on.

    Two mismatches previously made every lookup miss, so every word on the
    page was flagged "not in lexicon" — a flood of false suggestions on
    exactly the polytonic material this project targets:

    * **Normalization.** Bundled lexicons are NFC. The lookup used
      ``line.text`` raw, and Greek OCR output is not guaranteed NFC — this
      very method raises a ``unicode_nfc`` suggestion about that three checks
      earlier, then ignored it here. In NFD, ``εὐαγγέλιον`` is a different
      string and never matches.
    * **Punctuation.** ``str.split()`` breaks on whitespace only, so
      ``θεοτόκος,`` carried its comma into the lookup.

    Only the *lookup* is normalized. The suggestion still reports the original
    token, so nothing rewrites the recognized text (CLAUDE.md rule 1).
    """
    normalized = unicodedata.normalize("NFC", token)
    return normalized.strip("".join(_PUNCTUATION)).lower()


# Editorial punctuation that attaches to a word in printed Greek. Greek uses
# ano teleia (·) and the erotimatiko (;) where English uses ; and ?.
_PUNCTUATION: frozenset[str] = frozenset(".,;:·!?()[]{}«»\"'’‘“”—–-…")


class SuggestOnlyCorrector(IPostCorrector):
    """Run conservative checks while preserving the recognized source text.

    NFC normalization, diacritic validation, ligature/abbreviation
    expansion, and lexicon highlighting — all reversible, all
    suggest-only. Source text is never modified in place.
    """

    _BREATHING_MARKS: frozenset[str] = frozenset(
        ["\u0313", "\u0314"]  # smooth, rough
    )
    _ACCENT_MARKS: frozenset[str] = frozenset(
        ["\u0300", "\u0301", "\u0342"]  # grave, acute, circumflex/perispomeni
    )

    _ABBREVIATIONS: dict[str, str] = {
        "κ.τ.λ.": "καὶ τὰ λοιπά",
        "κ.λπ.": "καὶ λοιπά",
        "κ.α.": "καὶ ἄλλα",
        "κ.ο.κ.": "καὶ οὕτω καθεξῆς",
        "μ.Χ.": "μετὰ Χριστόν",
        "π.Χ.": "πρὸ Χριστοῦ",
        "π.χ.": "παραδείγματος χάριν",
        "βλ.": "βλέπε",
        "δηλ.": "δηλαδή",
        "στ.": "στίχος",
        "κεφ.": "κεφάλαιον",
        "σελ.": "σελίς",
        "τόμ.": "τόμος",
        "ἴδε": "ὅρα",
        "μτφ.": "μετάφρασις",
        "κ.ἐ.": "καὶ ἑξῆς",
        "κ.τ.τ.": "καὶ τὰ τοιαῦτα",
    }

    def __init__(
        self,
        lexicons: Mapping[Script, ILexicon] | None = None,
        ligatures: Mapping[str, str] | None = None,
        abbreviations: Mapping[str, str] | None = None,
    ) -> None:
        self._lexicons = dict(lexicons) if lexicons is not None else {}
        self._ligatures = dict(ligatures) if ligatures is not None else {}
        self._abbreviations = (
            dict(abbreviations)
            if abbreviations is not None
            else SuggestOnlyCorrector._ABBREVIATIONS
        )

    def correct(
        self, line: OCRLine, context: TenantContext
    ) -> Result[Sequence[Suggestion], EngineError]:
        suggestions: list[Suggestion] = []
        normalized = unicodedata.normalize("NFC", line.text)
        if normalized != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=normalized,
                    reason="unicode_nfc",
                    reversible=True,
                )
            )

        expanded = line.text
        for source, replacement in self._ligatures.items():
            expanded = expanded.replace(source, replacement)
        if expanded != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=expanded,
                    reason="reversible_ligature_expansion",
                    reversible=True,
                )
            )

        expanded_text = line.text
        for abbr, expansion in self._abbreviations.items():
            if abbr in line.text:
                expanded_text = expanded_text.replace(abbr, expansion)
        if expanded_text != line.text:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=expanded_text,
                    reason="reversible_abbreviation_expansion",
                    reversible=True,
                )
            )

        for index, character in enumerate(line.text):
            if not unicodedata.category(character).startswith("M"):
                continue
            previous_category = unicodedata.category(line.text[index - 1]) if index else ""
            if previous_category.startswith(("L", "M")):
                continue
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=line.text,
                    reason="dangling_combining_mark",
                    reversible=True,
                )
            )

        lexicon = self._lexicons.get(line.script)
        if lexicon is not None:
            for token in line.text.split():
                lookup = _lexicon_key(token)
                if not lookup or lexicon.contains(lookup):
                    continue
                # A bare "not in lexicon" flag hands the reviewer a problem
                # with no proposal. Offer OCR-plausible readings the lexicon
                # does recognize, best first (ENHANCEMENT_PLAN A8b), and fall
                # back to the bare flag when nothing plausible is found —
                # inventing a candidate would be worse than admitting none.
                # Pass the layers, not the composite: `rank` proposes from
                # earlier layers first, so a curated dialect reading outranks
                # a bulk one. Collapsing to a single lexicon would keep the
                # union but lose the precedence that protects regional forms.
                layers = getattr(lexicon, "layers", None) or [lexicon]
                proposals = confusion.suggest(lookup, list(layers))
                for proposal in proposals:
                    suggestions.append(
                        Suggestion(
                            line_id=line.id,
                            source_text=token,
                            suggestion_text=proposal,
                            reason=confusion.SUGGESTION_REASON,
                            reversible=True,
                        )
                    )
                if not proposals:
                    suggestions.append(
                        Suggestion(
                            line_id=line.id,
                            source_text=token,
                            suggestion_text=token,
                            reason=f"not_in_{lexicon.name}_lexicon",
                            reversible=True,
                        )
                    )

        diacritic_issues = self._check_diacritics(line.text)
        for issue in diacritic_issues:
            suggestions.append(
                Suggestion(
                    line_id=line.id,
                    source_text=line.text,
                    suggestion_text=line.text,
                    reason=issue,
                    reversible=True,
                )
            )

        return Ok(tuple(suggestions))

    @staticmethod
    def _check_diacritics(text: str) -> list[str]:
        """Check for impossible polytonic diacritic combinations.

        Returns a list of reason strings, one per detected violation.
        """
        issues: list[str] = []
        nfd = unicodedata.normalize("NFD", text)
        index = 0
        while index < len(nfd):
            character = nfd[index]
            if unicodedata.category(character) in ("Mn", "Mc"):
                index += 1
                continue
            breathing = 0
            accents = 0
            index += 1
            while index < len(nfd) and unicodedata.category(nfd[index]) in ("Mn", "Mc"):
                mark = nfd[index]
                if mark in SuggestOnlyCorrector._BREATHING_MARKS:
                    breathing += 1
                elif mark in SuggestOnlyCorrector._ACCENT_MARKS:
                    accents += 1
                index += 1
            if breathing > 1:
                issues.append("multiple_breathing_marks")
            if accents > 1:
                issues.append("multiple_accent_marks")
        return issues


__all__ = ["SuggestOnlyCorrector"]
