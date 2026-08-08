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

## Reproducing

Kraken runs at roughly 60–90 s per page per model on CPU, so all three models
plus Tesseract take about 5 minutes for one page.

```bash
python -m omniocr.interfaces.cli run "PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf" --max-pages 1 --engine kraken --json
```
