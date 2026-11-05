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

### Preprocessing (ENHANCEMENT_PLAN A3), measured the same way

Tesseract `grc` over the same three fixtures, mean across pages:

| Chain | CER | WER |
|---|---|---|
| grayscale only (what shipped) | 0.1436 | 0.4479 |
| grayscale + deskew | 0.1491 | 0.4480 |
| **grayscale + despeckle** | **0.1224** | **0.3685** |
| grayscale + deskew + despeckle | 0.1222 | 0.3721 |

Despeckle is a **14.8% relative CER** and **17.7% relative WER** improvement
and wins every page. It is now the desktop composition root's default.

Deskew made things worse and is not wired in. These three pages are already
square, so the estimator buys an interpolation pass and a slightly wrong angle
for nothing. That is a fact about this corpus rather than about deskewing —
the stage recovers a known skew to within 0.2° in the unit tests — so the code
stays and the default does not. Revisit when a scan-tier fixture is crooked.

Adding deskew on top of despeckle changes CER by 0.0002 and makes WER worse,
which is noise at n=3. Two stages are not better than one here.

**The gate sees this as of 2026-09-05.** It did not at first: `engine_baselines.json`
and `tests/test_engine_accuracy.py` called the engine on raw fixture bytes, so
they measured the engine rather than the product. Despeckle's 14.8% was
invisible to them, and so would its removal have been. Both now run the
composition root's `_default_image_processor()`, imported rather than
reconstructed, so the two cannot drift apart.

The committed baselines moved accordingly, and these are the figures to cite:

| Fixture | CER (through the pipeline) | WER |
|---|---|---|
| `polytonic-scan-1` | 0.1191 | 0.3728 |
| `polytonic-scan-2` | 0.1653 | 0.4062 |
| `polytonic-scan-3` | 0.0828 | 0.3264 |
| **mean** | **0.1224** | **0.3685** |

The four synthetic fixtures did not move at all, which is itself a check:
despeckle has nothing to remove from a clean PIL render, so any change there
would have meant the stage was eating real glyphs.

### Variant ensembling does not pay on this corpus (2026-09-05)

ENHANCEMENT_PLAN A3 predicts 10–30% relative CER from recognizing each page
from N differently binarized images and merging the readings. Measured through
the real pipeline — Tesseract layout, per-line candidates, A4's merge — over
the three scan fixtures:

| Variants | Chosen CER | Merged CER | vs 1-variant | Merges fired |
|---|---|---|---|---|
| 1 | **0.1226** | 0.1226 | — | 0 |
| 2 | 0.1283 | 0.1283 | 4.6% worse | 0 |
| 3 | 0.1306 | 0.1290 | 5.2% worse | 64 |
| 4 | 0.1277 | 0.1230 | 0.3% worse | 56 |

**Every configuration is worse than the baseline.** A3's DoD requires that no
variant configuration be worse than plain grayscale; this fails it. The
feature stays in the tree, opt-in and defaulted off, but it must not be
described as an improvement.

Two mechanisms, both legible in the numbers:

1. **More candidates make the *chosen* line worse**, before any merge happens.
   `ScriptAwareReconciler` falls back to highest confidence, and Tesseract
   reports confident nonsense on binarized input — the same failure this
   repository already documents for Kraken on Latin URLs. Each extra variant
   is another chance for a confident wrong reading to win the line.
2. **Two variants cannot help, by construction.** With two candidates every
   disagreement is a 1–1 tie, ties go to the pivot, so the merge is a no-op:
   `merges=0` across 136 `SPLIT` lines. Only three or more candidates can
   outvote the pivot.

The merge does repair some of the damage the fan-out causes (3.7% relative at
four variants) but never recovers the baseline. It is cleaning up a mess that
would not exist without it.

#### Follow-up, 2026-09-09: the defect was in selection, not in variants

The table above measures a pipeline that let variants *compete* for the line.
That was the bug. A variant is the same engine on transformed input, so
letting one win selection means trusting self-reported confidence to say which
binarization the engine read better — the one signal this codebase keeps
catching in a lie. Selection now sees only the primary candidate; every
variant still reaches the merge, where agreement between independent readings
is real evidence.

| Variants | Chosen CER (before) | Chosen CER (after) | Merged CER (after) |
|---|---|---|---|
| 1 | 0.1226 | 0.1226 | 0.1226 |
| 3 | 0.1306 | **0.1237** | 0.1249 |
| 4 | 0.1277 | **0.1237** | **0.1217** |

The monotone degradation is gone: chosen CER is now identical at 3 and 4
variants (WER 0.3700 for both), so selection no longer tracks variant count.

It does not pin to exactly the 1-variant baseline, and the reason is specific
rather than noise. The filter ends in a fallback to all candidates, so on a
segment where the primary produced no blocks but a variant did, the variant is
still selectable — two such segments in this corpus. That fallback is correct,
since a variant reading beats no line at all, so the accurate claim is that
**variants affect selection only where the primary produced nothing.**

At four variants the merged output (0.1217) edges below the single-variant
baseline (0.1226) for the first time. That is 0.7% relative on three pages:
noise, not a result, and still nothing like the predicted 10–30%. What changed
is the sign — ensembling is no longer actively harmful.

What this does **not** show is that variant ensembling is worthless in
general. These three fixtures are clean 300 DPI scans of one well-printed
book, where Otsu and Sauvola have little to disagree about beyond Sauvola
being worse. The technique targets degraded material — shadowed gutters,
photographed pages, uneven illumination — and this corpus contains none of it.
Re-measure once A1 includes a genuinely degraded variety before drawing a
general conclusion in either direction.

Still open: five of the six target varieties (modern, ancient, Byzantine,
Pontian, critical-edition apparatus) have no scan-tier fixture at all, and all
three that exist come from one book by one publisher. `docs/ENHANCEMENT_PLAN.md`
A1 sizes the real corpus at 115 pages.

## Update, 2026-11-03: `polytonic-scan-1` ground truth was missing two lines

The transcription jumped from "…πραγματικότητα." straight to "Τοῦτο
φαίνεται…", dropping two printed lines ("Τὸ βυζαντινὸ κράτος … διά-/σωσή
του."). Every engine that read them was charged for text the reference did
not contain, so every scan-1 number above is pessimistic. Restored from a
full-resolution crop of the page, not from engine output.

Re-measured against the corrected reference. Tesseract is `grc` from
`tessdata_fast` through `_default_image_processor()` — the variant that
reproduces every other committed baseline exactly; `tessdata` and
`tessdata_best` do not. Kraken is the manifest default, 7.1.1 on CUDA, fed raw
fixture bytes as `test_kraken_beats_tesseract_on_the_scan_corpus` feeds it.

| Fixture | Kraken CER | Tesseract CER | Kraken WER | Tesseract WER |
|---|---|---|---|---|
| `polytonic-scan-1` | **0.0499** | 0.0761 | **0.2021** | 0.3200 |
| `polytonic-scan-2` | 0.1775 | **0.1653** | 0.4509 | **0.4062** |
| `polytonic-scan-3` | **0.0629** | 0.0828 | **0.2562** | 0.3264 |
| **mean** | **0.0968** | 0.1081 | **0.3031** | 0.3509 |

Tesseract moved only on scan-1 (0.1191 → 0.0761), as a reference fix should.
Kraken also moved on scan-2 and scan-3, whose references did not change
(0.1940 → 0.1775, 0.0780 → 0.0629), so part of its shift is the setup — Kraken
version, device, or input path — not the fix. That is not separated here.
The conclusion is unchanged: Kraken wins the corpus and loses scan-2.

## Update, 2026-11-04: Unlimited-OCR-2 as an opt-in voter

[`baidu/Unlimited-OCR-2`](https://huggingface.co/baidu/Unlimited-OCR-2), a
dedicated document-OCR VLM, was run on the three scan fixtures through the
model's own `model.infer` (bf16, one 8 GB laptop GPU, ~140–175 s and 7.4 GB peak
VRAM per page). Its block markup was stripped to plain text and scored with
`omniocr.application.metrics` against the corrected references, beside the
Kraken and Tesseract numbers from the table above. Those come from different
input paths (Kraken on raw fixture bytes, Tesseract through
`_default_image_processor()`), so this is a comparison of setups on three
pages of one book, not a controlled engine bake-off.

| Fixture | Unlimited-OCR-2 CER | Kraken CER | Tesseract CER | Unlimited-OCR-2 WER |
|---|---|---|---|---|
| `polytonic-scan-1` | 0.0541 | **0.0499** | 0.0761 | 0.2084 |
| `polytonic-scan-2` | **0.0801** | 0.1775 | 0.1653 | 0.3013 |
| `polytonic-scan-3` | 0.0684 | **0.0629** | 0.0828 | 0.2893 |
| **mean** | **0.0675** | 0.0968 | 0.1081 | **0.2663** |

It has the best mean, mostly by not collapsing on scan-2, the page where both
box-grounded engines fail. It hallucinated no text. But its errors include
diacritic swaps that change meaning (`ἡ` read as `ἢ`), and it gives no
per-character confidence, so it is wired as one more voter, never as the
source (CLAUDE.md rule 1):

- **Opt-in.** `omniocr run … --unlimited-ocr-url http://host:30000/v1` adds it
  for the polytonic, ancient and Byzantine scripts on the `kraken` and
  `ensemble` engines. The CLI rejects the flag with `--engine tesseract` or any
  other `--script`, where it would never be called. The model is served
  separately behind an OpenAI-compatible endpoint (e.g. SGLang); the adapter
  imports no torch.
- **Zero confidence.** Its line blocks lose every confidence-weighted choice
  and mostly reach the output as aligned merge suggestions for the reviewer.
  On scan-1 it gave a candidate on 49 of 50 lines. One path does select its
  text: `ScriptAwareReconciler`'s homoglyph rule prefers the candidate that
  stayed in Greek script, so on a line where Kraken and Tesseract both emit
  Latin lookalikes (typically a majuscule heading) its reading becomes the
  line, unaudited diacritics included.
- **Guarded by a circuit breaker, not retried.** A failure (usually a
  600 s timeout) skips the engine for one timeout's length instead of
  resubmitting the page.

**End to end, 2026-11-04.** The real `UnlimitedOCREngine.extract` was then
driven over HTTP (`scripts/check_unlimited_ocr.py`) against
`scripts/serve_unlimited_ocr.py`, a minimal OpenAI-compatible server wrapping
the same `model.infer` (SGLang itself is Linux-only). It ran twice:

- **With the model card's n-gram guard** (`no_repeat_ngram_size=35`,
  `ngram_window=128`), as the table above was measured: 0.0541 / 0.0801 /
  0.0684 CER, 95–136 s per page.
- **Without it**, as a default server decodes, since the adapter sends no
  sampling options beyond `temperature` 0: identical text and CER, at 1,915 /
  1,816 / 1,832 output tokens either way, 188–231 s per page on the same GPU.

So on these pages the guard changes nothing, and the adapter's request
(prompt `"Multi page parsing."`, `max_tokens` 8192) and parser reproduce the
model's own output. Nothing came near `max_tokens`; the truncation path
(`finish_reason` `length`, which this server derives from the token count) was
not exercised.

Limits: not yet run through SGLang, whose chat template and image
preprocessing may differ from `model.infer`; a page that makes the model
repeat itself could behave differently without the guard. The fixtures are
2008×2835 px, under the 3508 px cap, so no downscaling took place. Three
pages of one book, as above.

## Update, 2026-11-05: model choice on the ensemble path, and fine-tuning

Same three scan fixtures, full pipeline (`omniocr run`, Tesseract + Kraken ensemble), mean over
pages:

| Configuration | CER | WER |
|---|---|---|
| Tesseract alone | 0.1055 | 0.3446 |
| ensemble + `porson` | 0.1227 | 0.3743 |
| ensemble + `bsb10234118` (manifest default) | 0.0872 | 0.2755 |
| ensemble + `sophokle1v3soph` | 0.0775 | 0.2398 |

`sophokle1v3soph` beats the manifest default on all three pages. This disagrees with the ranking
above, which came from one Kraken-only page. The default is unchanged: three pages of one book
are too few to overturn it. `omniocr run --model-select bakeoff` picks per document.

**Fine-tuning works.** Leave-one-page-out: fine-tune `sophokle1v3soph` on two pages, score the
third. Training lines are the segmenter's own crops paired with the human transcription by fuzzy
match (43-48 lines per page, mean CER 0.03-0.06 between the paired lines). 12 epochs, learning
rate 1e-4, CPU, about 9.6 minutes per fold.

| Held-out page | CER base → tuned | WER base → tuned |
|---|---|---|
| scan 1 | 0.0411 → 0.0324 | 0.1726 → 0.1263 |
| scan 2 | 0.1430 → 0.1247 | 0.3527 → 0.2612 |
| scan 3 | 0.0485 → 0.0334 | 0.1942 → 0.1260 |
| mean | 0.0775 → 0.0635 | 0.2398 → 0.1712 |

About 18% fewer character errors and 29% fewer word errors. Limits: one book and typeface,
about 90 training lines, one hyperparameter setting, no held-out book. Expect less on a
different face.

## Reproducing

Kraken runs at roughly 60–90 s per page per model on CPU, so all three models
plus Tesseract take about 5 minutes for one page.

Tesseract baselines need the `grc` and `ell` packs from
[`tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast). The
standard `tessdata` and `tessdata_best` packs are different models and do not
reproduce `tests/corpus/engine_baselines.json`.

```bash
python -m omniocr.interfaces.cli run "PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf" --max-pages 1 --engine kraken --json
```

Unlimited-OCR-2 needs a CUDA GPU (7.4 GB peak) and a separate environment
with the model card's `torch`/`transformers` pins, which clash with Kraken's.
Serve the downloaded model from that environment, then score from this one:

```bash
python -I scripts/serve_unlimited_ocr.py /path/to/Unlimited-OCR-2 --port 30000
```

```bash
python scripts/check_unlimited_ocr.py http://127.0.0.1:30000/v1
```

Add `--ngram-guard` to the server for the model card's decoding settings.
