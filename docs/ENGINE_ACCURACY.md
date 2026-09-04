# Engine accuracy on real printed Greek

**Status:** first measurement of this project's engines against a real page.
**Date:** 2026-08-07
**Page:** `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf`, page 31 (0-indexed 30),
rendered at 200 DPI — a mid-20th-century Greek book on the Byzantine monuments
of Thessaloniki, set in a Didot-style Greek face with polytonic orthography.

Until now every accuracy number in this repository came from
`tests/corpus/*.png`, which are PIL renders of Arial (`implementation_plan.md`
F-2). Those measure a font round-trip, not recognition. This document is the
first evidence about the material the project actually targets.

## Results

Character error rate, lower is better. Each ground-truth line is scored
against the engine line that matches it best, so line-alignment is not a
confound.

| Engine / model | CER | Notes |
|---|---|---|
| **kraken `greek-german_serifs_bsb10234118`** | **0.038** | manifest default |
| tesseract `grc` | 0.056 | |
| kraken `greek-german_serifs_sophokle1v3soph` | 0.147 | |
| kraken `greek-english_porson_sophoclesplaysa05campgoog` | 0.264 | was the default, by filename |

### What this changes

1. **`CLAUDE.md` rule 2 holds — conditionally.** Kraken *is* the accuracy
   driver for this material, but only with a model whose training typeface
   matches the page. With the wrong model it is 4.7x worse than Tesseract.
   The rule should be read as "Kraken with an appropriate model", not
   "Kraken".

2. **Model choice dominates engine choice.** The spread across three Kraken
   models (0.038 → 0.264, a factor of 7) is far wider than the gap between
   the best Kraken model and Tesseract (0.038 → 0.056). Picking the model is
   the higher-leverage decision.

3. **The default was chosen by `sorted()`.** `_resolve_model` used
   `sorted(glob("models/*.mlmodel"))[0]`, so the alphabetically first
   filename won — and it was the worst of the three. The manifest now carries
   an explicit `"default": true`, and `ModelManifest.default_for()` reads it.

### Why `porson` is the worst

All three are genuine Greek models from the Ciaconna family (arXiv
2110.06817). The differences are typographic, not linguistic:

- `porson` is trained on the Porson Greek typeface used in 18th–19th century
  English classical editions. It is also a *Greek-English* model, so Latin
  letters are in its codec — which is why its failures are systematically
  Latin substitutions for visually similar Greek: `cynμαrίζsrαι` for
  `σχηματίζεται` (σ→c, χ→y, τ→r, ε→s), `sivαι` for `εἶναι`.
- The `german_serifs` models are trained on 19th century German-edition Greek,
  a serif face much closer to this book's Didot-style setting.

## Ground truth

Hand-transcribed by eye from a 400–700 DPI render, not copied from any
engine's output. Diplomatic: `μικροσκωπικοῦ` keeps the book's own ω (the
standard spelling is `μικροσκοπικοῦ`).

```
λου του, ὅπου σχηματίζεται ἕνα εἶδος μικροσκωπικοῦ ὀροπεδίου καὶ ἁπλώ-
νουμε εὐχάριστα τὸ βλέμμα μας ἐπάνω ἀπὸ τὴν θάλασσαν τῶν οἰκοδομῶν
μέχρι τὴν ἁλίκτυπο παραλία.
ΑΓΙΑ ΣΟΦΙΑ
Ἡ νέα ἐξόρμηση στὴν περιήγησή μας ἀρχίζει ἀπὸ τὸ μεγάλο ναὸ τῆς
```

The face prints the kappa- and theta-symbol variants (ϰ, ϑ); both engines emit
the standard letters. CER is reported with those folded, so the comparison is
about recognition rather than about which codepoint a font uses. Folding
changed the numbers by ≤0.003.

## Limitations — read before citing these numbers

- **Five lines, one page, one book.** This is enough to show that model
  selection was wrong and to justify a default. It is not a benchmark. F-2 in
  `implementation_plan.md` (a real corpus with per-variety ground truth)
  remains open, and these numbers must not be presented as closing it.
- **Nothing here covers ancient, Byzantine, or Pontian material.** The page is
  modern Greek prose in polytonic orthography. A different default may well be
  right for critical editions in Porson type — which is, after all, what
  `porson` was trained for.
- **Tesseract's number is sensitive to line composition.** A first attempt
  grouped its word boxes with an ad-hoc row heuristic and produced 0.298; run
  through the pipeline's own `_assign_blocks`, the same output scores 0.056.
  The engine did not change, the line assembly did. Any future comparison must
  put both engines through the real pipeline path.

## Update, 2026-09-03: three scan-tier fixtures

The corpus now holds three real pages from the same book with human-reviewed
diplomatic transcriptions (`polytonic-scan-1/2/3`, pages 21, 35 and 49,
rendered at 300 DPI). They are the first fixtures the accuracy gates may
legitimately compute baselines from — the four Arial renders are marked
`synthetic` in `tests/corpus/PROVENANCE.json` and are excluded from any
accuracy claim.

| Fixture | Kraken CER | Tesseract CER | Kraken WER | Tesseract WER |
|---|---|---|---|---|
| `polytonic-scan-1` | **0.1066** | 0.1366 | **0.2610** | 0.4364 |
| `polytonic-scan-2` | 0.1940 | **0.1854** | **0.4509** | 0.4799 |
| `polytonic-scan-3` | **0.0780** | 0.1065 | **0.2562** | 0.4194 |
| **mean** | **0.1262** | 0.1428 | **0.3227** | 0.4452 |

Kraken is the manifest default `greek-german_serifs_bsb10234118`; Tesseract is
`grc`. Both were run through the pipeline path, per the limitation noted above.

### What this changes

1. **Rule 2 survives contact with a second measurement, and gets narrower.**
   Kraken wins the corpus on CER and wins every page on WER, by a wide margin
   (0.32 against 0.45). But it *loses* `polytonic-scan-2` on CER. The gate in
   `tests/test_engine_accuracy.py` is therefore an aggregate over the scan
   tier, not a per-page assertion — excluding the page that disagrees would
   make the suite assert what we wish were true.

2. **The 0.038 headline does not generalize.** Measured on three further
   pages, the same model scores 0.078–0.194. Two confounds are unresolved and
   should not be papered over: the 0.038 page was rendered at a higher DPI
   than these three, and it was a single page of clean body prose whereas
   `scan-2` carries a two-column hymn quotation. The honest summary is that
   this model reads this book at roughly **0.08–0.19 CER**, and that one page
   at 0.038 was optimistic rather than representative.

3. **The synthetic fixtures were flattering by an order of magnitude.**
   Tesseract scores 0.0000–0.0135 on the Arial renders and 0.1065–0.1854 on
   real scans of the same script. `tests/corpus/README.md` warned about
   exactly this; the numbers now quantify it. The plausible-CER ceiling in the
   accuracy tests is tiered accordingly (0.15 synthetic, 0.30 scan).

4. **Neither engine is anywhere near shippable on this material.** A WER of
   0.32 means roughly one word in three needs a human touch. This is the
   baseline that A2 (fine-tuning) and A3 (preprocessing) have to beat, and it
   is the first honest starting point the project has had.

Still open: five of the six target varieties (modern, ancient, Byzantine,
Pontian, critical-edition apparatus) have no scan-tier fixture at all, and all
three that exist come from one book by one publisher. `docs/ENHANCEMENT_PLAN.md`
A1 sizes the real corpus at 115 pages.

## Reproducing

Kraken runs at roughly 60–90 s per page per model on CPU, so all three models
plus Tesseract take about 5 minutes for one page.

```bash
python -m omniocr.interfaces.cli run "PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf" --max-pages 1 --engine kraken --json
```
