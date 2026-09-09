"""Fetch, normalize and freeze the bulk Greek lexicons (ENHANCEMENT_PLAN A8a).

Until this ran, only ``byzantine`` (59 words) and ``pontian`` (58) existed.
Polytonic, ancient and modern — the project's primary targets — had no lexicon
at all, which silently zeroed A6's 0.4 lexicon scoring term and left
``SuggestOnlyCorrector`` with nothing to check polytonic pages against.

Run from the repository root:

    python scripts/build_lexicon.py            # fetch, build, write blobs
    python scripts/build_lexicon.py --dry-run  # report counts, write nothing

Output is one sorted newline-joined UTF-8 blob per lexicon under
``packages/omniocr/src/omniocr/data/lexicons/``, plus a ``PROVENANCE.json``
recording each source's URL, licence, sha256 and form count. The blobs are
committed: a lexicon fetched at install time is a network dependency and a
failure mode, and it makes a build unreproducible.

**Sources are parsed, never executed.** The CLTK lemmata file is a 43 MB
Python module; this reads it as text and scans for dictionary keys rather than
importing it. Running a downloaded file to read data from it would be a
remote-code-execution hole for a build script.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

OUTPUT_DIR = Path("packages/omniocr/src/omniocr/data/lexicons")
PROVENANCE = OUTPUT_DIR / "PROVENANCE.json"

_USER_AGENT = "omniocr-lexicon-build (https://github.com/georgehadji/ocr)"
_TIMEOUT_SECONDS = 120

# Greek letter ranges: basic Greek/Coptic plus Greek Extended (polytonic).
_GREEK = re.compile(r"^[Ͱ-Ͽἀ-῿ͅ]+$")

# Keys of the CLTK LEMMATA dict literal. Greek forms never contain a straight
# ASCII apostrophe — elision is written with U+1FBD/U+2019 — so a single-quote
# delimited scan is unambiguous here. Checked against the file header.
_CLTK_KEY = re.compile(r"'([^']+)':")


@dataclass(frozen=True, slots=True)
class Source:
    """One upstream word list and how to turn it into forms."""

    key: str
    name: str
    url: str
    licence: str
    parse: Callable[[bytes, str], Iterable[str]]
    gzipped: bool = False
    # Not every list is UTF-8. The Greek Hunspell dictionary is ISO-8859-7,
    # and decoding it as UTF-8 with errors="replace" produced 828,806 lines of
    # mojibake that `collect` then discarded as non-Greek — an empty lexicon
    # reported as a clean run. Encoding is explicit per source for that reason.
    encoding: str = "utf-8"


def _decode(payload: bytes, encoding: str) -> str:
    """Strict decode: a wrong encoding must fail loudly, not silently mangle."""
    return payload.decode(encoding)


def _parse_hunspell(payload: bytes, encoding: str) -> Iterable[str]:
    """Stems from a Hunspell ``.dic``.

    The first line is a count, and each entry is ``stem/FLAGS``. Affix flags
    are dropped rather than expanded: expanding them needs the ``.aff``
    ruleset, and the stems alone already cover the inflected forms that matter
    for a *membership* test, which is the whole of ``ILexicon``.
    """
    text = _decode(payload, encoding)
    for line in text.splitlines()[1:]:
        stem = line.split("/", 1)[0].strip()
        if stem:
            yield stem


def _parse_cltk_lemmata(payload: bytes, encoding: str) -> Iterable[str]:
    """Inflected forms from the CLTK lemmata map, by scanning — never importing."""
    text = _decode(payload, encoding)
    for match in _CLTK_KEY.finditer(text):
        yield match.group(1)


def _parse_titles(payload: bytes, encoding: str) -> Iterable[str]:
    """Page titles from a MediaWiki all-titles dump (one per line, header first)."""
    text = _decode(payload, encoding)
    for line in text.splitlines()[1:]:
        title = line.strip().replace("_", " ")
        if title and " " not in title:
            yield title


SOURCES: tuple[Source, ...] = (
    Source(
        key="ancient",
        name="CLTK Greek lemmata (Perseus-derived)",
        url=(
            "https://raw.githubusercontent.com/cltk/greek_models_cltk/"
            "master/lemmata/greek_lemmata_cltk.py"
        ),
        licence="MIT (cltk/greek_models_cltk)",
        parse=_parse_cltk_lemmata,
    ),
    Source(
        key="modern",
        name="Hunspell el_GR (LibreOffice dictionaries)",
        url=("https://raw.githubusercontent.com/LibreOffice/dictionaries/master/el_GR/el_GR.dic"),
        licence="GPL-2.0 / LGPL-2.1 / MPL-1.1 tri-license (el_GR.aff header)",
        parse=_parse_hunspell,
        encoding="iso-8859-7",
    ),
    Source(
        key="wiktionary",
        name="Greek Wiktionary page titles (ns0)",
        url=(
            "https://dumps.wikimedia.org/elwiktionary/latest/"
            "elwiktionary-latest-all-titles-in-ns0.gz"
        ),
        licence="CC-BY-SA-4.0 (Wikimedia)",
        parse=_parse_titles,
        gzipped=True,
    ),
)


def fetch(source: Source) -> bytes:
    request = urllib.request.Request(source.url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
        payload: bytes = response.read()
    return gzip.decompress(payload) if source.gzipped else payload


def normalize(form: str) -> str:
    """NFC-normalize and lowercase, matching how lexicons are keyed.

    Unicode hygiene only, never an orthographic change (CLAUDE.md rule 5).
    """
    return unicodedata.normalize("NFC", form).lower()


_ACCENTS = frozenset(["̀", "́", "͂", "̓", "̔", "ͅ"])


def monotonize(form: str) -> str:
    """Strip breathings, accents and iota subscript. Mirrors ports/lexicon."""
    decomposed = unicodedata.normalize("NFD", form)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _ACCENTS))


def collect(source: Source, payload: bytes) -> set[str]:
    """Greek-script forms only.

    Every list carries noise the others do not: Wiktionary holds English
    glosses and template names, Hunspell holds Latin abbreviations. A
    non-Greek entry in a Greek lexicon is worse than a missing one, because it
    makes a mixed-script OCR error *validate*.
    """
    forms = {
        normalized
        for form in source.parse(payload, source.encoding)
        if _GREEK.match(normalized := normalize(form)) and len(normalized) > 1
    }
    # Store ONLY the accent-stripped form.
    #
    # Not an orthographic change: both spellings are stored, and the lexicon
    # only ever answers yes/no. It is what makes the list usable at all on
    # this project's material. Measured 2026-09-09 without it, an exact-match
    # lexicon flagged 309 of 456 tokens on a *human-transcribed* page - 68% of
    # definitionally correct text - because the target books are modern Greek
    # in polytonic orthography, and no monotonic word list matches a polytonic
    # spelling. SetLexicon has always done this via a monotonized index; a
    # sorted blob cannot, so the variants go in the blob instead.
    # `ILexicon` answers yes/no and nothing else, and `contains` already
    # monotonizes the query before its second probe - so storing the accented
    # spellings too doubles the committed data (104 MB against 55 MB) to
    # answer exactly the same questions. The accented forms are recoverable
    # from the source lists at any time; the blob is an index, not an archive.
    return {monotonize(form) for form in forms}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    parser.add_argument(
        "--only", default=None, help="build a single source by key (ancient/modern/wiktionary)"
    )
    args = parser.parse_args()

    selected = [s for s in SOURCES if args.only is None or s.key == args.only]
    if not selected:
        raise SystemExit(f"no source with key {args.only!r}")

    # Carry forward entries for sources not rebuilt this run, so `--only`
    # refreshes one list without dropping the others from the provenance file.
    entries: dict[str, object] = {}
    if PROVENANCE.is_file():
        loaded = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        existing = loaded.get("sources") if isinstance(loaded, dict) else None
        if isinstance(existing, dict):
            entries = dict(existing)

    for source in selected:
        print(f"fetching {source.key}: {source.url}", file=sys.stderr)
        try:
            payload = fetch(source)
        except Exception as exc:  # noqa: BLE001 - one unreachable source must not kill the rest
            print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        forms = collect(source, payload)
        if not forms:
            # Silence here is how the ISO-8859-7 bug survived a full run: a
            # mis-decoded list filters down to nothing and looks like success.
            raise SystemExit(
                f"{source.key}: parsed 0 Greek forms from {len(payload):,} bytes. "
                "The source format or encoding changed - fix the parser rather "
                "than shipping an empty lexicon."
            )
        blob = ("\n".join(sorted(forms)) + "\n").encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()
        print(f"  {len(forms):,} Greek forms, blob {len(blob) / 1_048_576:.1f} MB")

        entries[source.key] = {
            "name": source.name,
            "url": source.url,
            "licence": source.licence,
            "forms": len(forms),
            "blob_sha256": digest,
        }
        if not args.dry_run:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / f"{source.key}.txt").write_bytes(blob)

    if not args.dry_run:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        PROVENANCE.write_text(
            json.dumps(
                {
                    "_comment": [
                        "Bulk lexicons built by scripts/build_lexicon.py.",
                        "Blobs are sorted, NFC-normalized, lowercased, Greek-script only,",
                        "one form per line, and are read by SortedBlobLexicon via bisect.",
                        "Re-run the script to refresh; review the form counts in the diff.",
                    ],
                    "sources": entries,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {OUTPUT_DIR}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
