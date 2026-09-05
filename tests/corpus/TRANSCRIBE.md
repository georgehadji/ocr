# Adding scan-tier fixtures

The repeatable procedure for growing the ground-truth corpus. The first three
polytonic pages were done by hand as a one-off; this is how the remaining ones
get done.

## Why this is the bottleneck

`docs/ENHANCEMENT_PLAN.md` A1 sizes the corpus at **115 pages**. We have 3.

| Variety | Pages needed | Have | Purpose |
|---|---|---|---|
| Polytonic 20c Didot | 30 | **3** | Fine-tune target |
| 19c German serif | 30 | 0 | Fine-tune target; two bundled models suit this face |
| Critical edition | 20 | 0 | Fine-tune target + the only A10 material |
| Byzantine / ligature | 15 | 0 | Hardest variety; needs the most training signal |
| Modern monotonic | 10 | 0 | Baseline sanity |
| Pontian | 10 | 0 | Post-correction path |

A2 fine-tuning is fully built — `KetosTrainer`, the `BeatsParentOnHeldOut`
promotion policy, the model registry, the router hand-off — and has never once
run, for want of ground truth. Nothing in the plan pays off until this table
fills in.

Measurement alone needs ~20 pages. Training wants 50–100 per typeface family.
Be clear which you are buying.

## 1. Source the pages

Public-domain digitisations cover most varieties: Internet Archive, the
Bayerische Staatsbibliothek (BSB), and Anemi. **Pontian is the acquisition
risk** — start looking for it before you need it.

Prefer pages that are *representative*, not clean. A corpus of unusually tidy
pages produces a model that is confident and wrong on ordinary ones. Include
the tight gutters, the show-through, and the pages where the ink is heavy.

## 2. Stage them

```bash
python scripts/stage_corpus_pages.py \
    --pdf "PDFs for OCR/Some Book.pdf" \
    --pages 12,40,61 \
    --prefix german-serif-scan \
    --script polytonic \
    --typeface "19c German serif" \
    --source-title "Full title as printed" \
    --licence "public domain (published 1887)" \
    --dry-run
```

Drop `--dry-run` to write. Page numbers are **0-based PDF indices**, matching
how the existing fixtures record them. The script renders each page at 300 DPI,
picks the next free `<prefix>-N`, and writes the `PROVENANCE.json` entry with
the source coordinates already filled in.

It deliberately writes no `.txt`. `list_fixture_ids()` globs `*.txt`, so a
staged page is inert until a human transcribes it — no gate can measure against
it, and nothing can quietly seed ground truth from OCR output.

## 3. Transcribe

For each staged image write `tests/corpus/<id>.txt`.

**Diplomatic** means reproduce what is printed, not what it should say:

- Keep the original orthography, accents, breathings, and abbreviations.
- Do not correct the source, modernise spelling, or expand abbreviations.
- Keep the printed variant letterforms where they are meaningful. The existing
  `polytonic-scan-*` files keep the book's own `μικροσκωπικοῦ` with ω, though
  the standard spelling has ο.
- Preserve line breaks as printed, one line per printed line.
- Multi-column sections: transcribe in reading order, left column fully, then
  right. `polytonic-scan-2.txt` is the worked example.
- UTF-8, NFC-normalized. `test_ground_truth_is_nfc_normalized` enforces this.

**Ground truth must come from a human reading the page.** Never seed it from
OCR output, not even as a starting draft you intend to correct — the errors
you fail to notice become the errors the model is trained to reproduce, and
they are invisible afterwards. An AI-drafted transcription reviewed line by
line against the image by a person is acceptable, and must be declared as such
in `transcribed_by`.

## 4. Complete the provenance

The staging script leaves two fields for you:

- `licence` — not optional. `test_scan_fixtures_carry_source_licence_and_transcriber`
  fails without it. If the book is not redistributable, say so plainly and move
  the image out of the repo rather than leaving the field vague.
- `transcribed_by` — who read the page. Replace the `PENDING` placeholder.

## 5. Recompute baselines

```bash
python scripts/compute_engine_baselines.py
```

Review the diff rather than rubber-stamping it. A rise in CER on an existing
fixture is an accuracy regression, not a new baseline.

## 6. Check what the new pages changed

New fixtures shift the aggregate, and two gates are calibrated against it:

- `MAX_PLAUSIBLE_CER` in `tests/test_engine_accuracy.py` is tiered (0.15
  synthetic, 0.30 scan). A genuinely harder variety may need its own entry —
  raise it from measured data with the date recorded, never to make a test pass.
- `test_kraken_beats_tesseract_on_the_scan_corpus` compares corpus means. If a
  new variety flips it, that is a finding about the default model, not a test
  to adjust. `docs/ENGINE_ACCURACY.md` is where it gets written up.

## Known gaps

The three existing fixtures are **one book, one publisher, one typeface**. Even
the polytonic figure is thinner than the count suggests. Byzantine in
particular needs a real Byzantine face with ligatures, which no source
currently in the repository contains.
