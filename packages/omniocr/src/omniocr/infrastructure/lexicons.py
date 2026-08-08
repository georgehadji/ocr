"""Bundled lexicons for Byzantine and Pontian Greek.

These are **suggest-only** vocabularies. They are intentionally scoped to
dialect-specific words so a generic corrector cannot "fix" legitimate
historical or regional forms into standard Greek.

Byzantine Greek (printed): liturgical, theological, and administrative
terms with characteristically Byzantine orthography.

Pontian: words and forms specific to the Pontian Greek dialect.
"""

from __future__ import annotations

from omniocr.domain.models import Script
from omniocr.ports.lexicon import SetLexicon

# `_load_words()` used to live here, reading `<name>.txt` beside this module.
# No such file was ever committed and nothing called it — the loader existed,
# the data did not. Deleted rather than left as a working-looking hook; see
# `implementation_plan.md` F-8 for the real-lexicon work it stood in for,
# which is blocked on sourcing and licensing, not on code.

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
