# Draft transcriptions — pending human verification

These three files are **AI-drafted diplomatic transcriptions** of the pages
named in `../TRANSCRIBE.md`, produced by reading the page images. They are
**not** yet ground truth.

## Why this directory and not `tests/corpus/`

`list_fixture_ids()` in `omniocr.testing.fixtures` globs `*.txt` directly
inside `tests/corpus/`. A file there is immediately live in the corpus:
`test_every_fixture_declares_its_tier` requires a `PROVENANCE.json` entry the
moment it appears, and once that entry declares `"tier": "scan"`, the file
backs an accuracy baseline. This directory is outside that glob on purpose —
nothing here is discovered by the harness, so nothing here can be silently
promoted to "verified" by an automated gate.

## Why the transcriptions are not trustworthy as-is

`tests/corpus/README.md` states the rule this exists to protect: "Ground
truth must come from a human reading the page. Never seed it from OCR
output." An AI reading a scanned page is not categorically different from an
OCR engine reading one — both are fallible on exactly the marks that matter
most here: breathing marks, iota subscripts, accent placement on ambiguous
glyphs, and where a printed line actually breaks. Presenting this draft as
verified ground truth would let those same errors into the one dataset that
is supposed to catch them.

## What to do with each file

1. Open the source image (`tests/corpus/polytonic-scan-N.png`) beside the
   draft and read every line against it. Pay particular attention to:
   - Breathing marks (smooth ᾿ vs rough ῾) — visually tiny at this
     resolution and the class of error most likely in this draft.
   - Iota subscripts and how they render on capitals (e.g. Ἅγ. vs Ἁγ.).
   - Line breaks — the draft attempts to preserve them as printed, but a
     hyphenated word split across a printed line is easy to place wrong.
   - `polytonic-scan-2.txt`: the two-column verse block at the bottom. It is
     transcribed left column fully, then right column, per
     `TRANSCRIBE.md`'s instruction — confirm that reading order is what you
     want, and check the `[ρετος` continuation marker, which reproduces an
     odd bracket in the original rather than correcting it.
2. Correct anything wrong directly in the draft.
3. Move the corrected file to `tests/corpus/<id>.txt` (NFC-normalized —
   already true of this draft, re-check after edits).
4. Fill in `PROVENANCE.json` per the template in `TRANSCRIBE.md`, including
   `licence` — the source PDF's redistribution status has not been
   determined here and must be before this becomes a committed fixture.
5. Run `python scripts/compute_engine_baselines.py`.

Until step 3, these drafts do nothing — no test reads this directory.
