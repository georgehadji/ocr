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

from omniocr.domain.models import Script
from omniocr.ports.interfaces import ILexicon
from omniocr.ports.lexicon import LayeredLexicon, SetLexicon, SortedBlobLexicon

# Bulk word lists built by scripts/build_lexicon.py and committed alongside
# the package, so a lexicon is never a network dependency at install time.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "lexicons"

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


def _blob(name: str) -> SortedBlobLexicon | None:
    """Load a bulk lexicon, or None when its blob is absent.

    Absence is survivable and must stay that way: a source checkout without
    the data files, or a future build that ships a subset, should degrade to
    the curated lexicons rather than failing to import.
    """
    path = _DATA_DIR / f"{name}.txt"
    return SortedBlobLexicon(name=name, path=path) if path.is_file() else None


def _layered(name: str, *candidates: ILexicon | None) -> ILexicon | None:
    layers = [layer for layer in candidates if layer is not None]
    if not layers:
        return None
    return layers[0] if len(layers) == 1 else LayeredLexicon(name=name, layers=layers)


def lexicons_by_script() -> dict[Script, ILexicon]:
    """Return a mapping of Script → lexicon.

    Curated dialect vocabularies come **first** in every layered entry. That
    order is load-bearing rather than cosmetic: `confusion.rank` proposes
    candidates layer by layer, so a Pontian or Byzantine reading outranks a
    standard-Greek one. Reversed, a bulk list would propose "corrections"
    that normalize exactly the regional forms this module exists to protect.

    Ancient forms back the polytonic scripts and modern forms the monotonic
    one, because the two barely overlap — 45,888 shared forms out of 1.7M
    when measured on 2026-09-09. Pointing a polytonic page at the modern list
    would let monotonic spellings validate as correct readings.
    """
    ancient = _blob("ancient")
    modern = _blob("modern")
    wiktionary = _blob("wiktionary")

    mapping: dict[Script, ILexicon] = {
        Script.BYZANTINE: LayeredLexicon(
            name="byzantine",
            layers=[layer for layer in (byzantine_lexicon(), ancient) if layer is not None],
        ),
        Script.PONTIAN: LayeredLexicon(
            name="pontian",
            layers=[
                layer for layer in (pontian_lexicon(), modern, wiktionary) if layer is not None
            ],
        ),
    }
    # Polytonic gets the modern lists as well as the ancient ones, and the
    # order matters. This project's target material is *modern Greek in
    # polytonic orthography* - 20th-century prose, not classical texts - so
    # ancient vocabulary alone covers almost none of it. Measured 2026-09-09
    # against ancient forms only, 309 of 456 tokens on a human-transcribed
    # page were flagged unknown: correct text, wrong word list.
    for script, lexicon in (
        (Script.ANCIENT, ancient),
        (Script.POLYTONIC, _layered("polytonic", ancient, modern, wiktionary)),
        (Script.MODERN, _layered("modern", modern, wiktionary)),
    ):
        if lexicon is not None:
            mapping[script] = lexicon
    return mapping
