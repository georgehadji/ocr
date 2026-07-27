"""Bundled lexicons for Byzantine and Pontian Greek.

These are **suggest-only** vocabularies. They are intentionally scoped to
dialect-specific words so a generic corrector cannot "fix" legitimate
historical or regional forms into standard Greek.

Byzantine Greek (printed): liturgical, theological, and administrative
terms with characteristically Byzantine orthography.

Pontian: words and forms specific to the Pontian Greek dialect.
"""

from __future__ import annotations

from pathlib import Path

from omniocr.ports.lexicon import SetLexicon
from omniocr.domain.models import Script


def _load_words(name: str) -> tuple[str, ...]:
    """Load a word list from a bundled text file (one word per line)."""
    path = Path(__file__).resolve().parent / f"{name}.txt"
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


_BYZANTINE_WORDS: tuple[str, ...] = (
    "θεοτόκος",
    "ἀκαθιστός",
    "εὐαγγέλιον",
    "λειτουργία",
    "εὐχή",
    "εὐλογία",
    "ἀντίφωνον",
    "κάθισμα",
    "τροπάριον",
    "κοντάκιον",
    "εἱρμός",
    "καταβασία",
    "στιχηρόν",
    "δοξαστικόν",
    "μακάριος",
    "ἀμήν",
    "ἀλληλούϊα",
    "κεκραγάριον",
    "προκείμενον",
    "ἀπόστολος",
    "εὐαγγελιστής",
    "ψαλτήριον",
    "τετραευαγγέλιον",
    "ὡρολόγιον",
    "εὐχολόγιον",
    "τυπικόν",
    "μηναῖον",
    "τριῴδιον",
    "πεντηκοστάριον",
    "παρακλητική",
    "ἁγιολογία",
    "συναξάριον",
    "ὑποτύπωσις",
    "διάταξις",
    "εἰσοδικόν",
    "κοινωνικόν",
    "μακαρισμός",
    "πολυέλεος",
    "μεγαλυνάριον",
    "ἐξαποστειλάριον",
    "φωταγωγικόν",
    "θεοτοκίον",
    "σταυροθεοτοκίον",
    "καθαρά",
    "ἑσπερινός",
    "ὄρθρος",
    "μεσονυκτικόν",
    "πρωΐ",
    "ὀψέ",
    "λιτή",
    "ἀρτοκλασία",
    "μετάνοια",
    "προσκύνησις",
    "εἰκόνα",
    "τέμπλον",
    "δεσποτικόν",
    "δεήσις",
    "λιτανεία",
    "εὐχή",
    "ἀγρυπνία",
)

_PONTIAN_WORDS: tuple[str, ...] = (
    "εμάν",
    "εμόν",
    "εσύ",
    "ατό",
    "ατού",
    "ατούν",
    "εμείς",
    "εσείς",
    "ατοί",
    "ατές",
    "ατά",
    "λέγω",
    "λαλῶ",
    "τραγωδώ",
    "έρκομαι",
    "πάω",
    "έφαγα",
    "έπια",
    "έκαμα",
    "ύπνος",
    "ύπνον",
    "εφτά",
    "οχτώ",
    "εννέα",
    "οσπίτιον",
    "έσπερα",
    "πρωΐ",
    "νύχτα",
    "ημέραν",
    "χρόνον",
    "καιρός",
    "άνθρωπος",
    "γυναίκα",
    "παιδίον",
    "πατέρας",
    "μάνα",
    "αδέρφιον",
    "ψωμίν",
    "νερόν",
    "κρέας",
    "γάλα",
    "τυρίν",
    "αβγόν",
    "λάδι",
    "κρασίν",
    "ράφτ’",
    "ράφτε",
    "τοίχος",
    "θύρα",
    "παράθυρον",
    "τραπέζιν",
    "καρέκλαν",
    "κλίνη",
    "μαχαίριν",
    "πιρούνιν",
    "κουτάλ’",
    "κουτάλιν",
    "φκιάριν",
)


def byzantine_lexicon() -> SetLexicon:
    """Return a suggest-only lexicon for Byzantine printed Greek."""
    return SetLexicon(name="byzantine", words=_BYZANTINE_WORDS)


def pontian_lexicon() -> SetLexicon:
    """Return a suggest-only lexicon for Pontian Greek."""
    return SetLexicon(name="pontian", words=_PONTIAN_WORDS)


def lexicons_by_script() -> dict[Script, SetLexicon]:
    """Return a mapping of Script → lexicon for all bundled lexicons."""
    return {
        Script.BYZANTINE: byzantine_lexicon(),
        Script.PONTIAN: pontian_lexicon(),
    }
