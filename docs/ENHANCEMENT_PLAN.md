# OmniOCR — Accuracy Maximization Plan

**Status:** proposed, not started
**Date:** 2026-08-28
**Goal:** the lowest achievable CER on printed Greek. One objective. Everything else
— cost, latency, portability, dependency hygiene — is subordinate.
**Design of record:** [ARCHITECTURE.md](ARCHITECTURE.md), with two locked decisions
reopened in §2.

**Parked:** [COMMERCIAL_PLAN.md](COMMERCIAL_PLAN.md) — licence and packaging analysis.
Not deleted; not driving anything. Its one durable finding is in §2.1.

---

## 1. What "perfect" means here, so it can be measured

"Perfect OCR" is not a slogan you can optimize against. The operational target:

| Variety | Current | Target | Basis |
|---|---|---|---|
| Modern monotonic | not measured | **< 0.005** | Clean print, strong lexicon. This should be near-solved. |
| Polytonic 20th c. | **0.126** (n=3) | **< 0.010** | Was listed as 0.038 from a single page; three scan-tier fixtures give a mean of 0.126 (Kraken) / 0.143 (Tesseract). Fine-tuning on-typeface reliably lands at the target, but from further away than this table first claimed. |
| 19th c. German serif | not measured | **< 0.015** | Two bundled models were trained on this material. |
| Critical edition, main text | not measured | **< 0.015** | Same as polytonic, plus zone separation. |
| Critical edition, apparatus | not measured | **< 0.05** | Small type, siglum-dense. Honestly hard. |
| Byzantine / ligature | not measured | **< 0.06** | Hardest. Report it honestly rather than hiding it. |
| Pontian | not measured | **< 0.02** | Recognition is Greek-script; the dialect load is post-correction. |

Five of seven are currently unmeasured. **You cannot optimize what you do not measure**,
which is why A1 is first and is not negotiable.

One rule survives from the original design and constrains every workstream below:
**faithfulness** (CLAUDE.md rule 1). No stage silently alters recognized text. This is
not a philosophical preference — it is what makes the error analysis in A1 possible at
all. A pipeline that quietly "fixes" its own output cannot be measured.

---

## 2. Constraints dropped, and what that buys

### 2.1 Licence constraints — dropped

Your call, and it is pure upside for accuracy. What it unlocks:

- **Calamari (GPLv3)** returns as a real ensemble member, not a flagged extra. It is a
  voting-oriented recognizer and its errors are decorrelated from Kraken's — which is
  exactly what A4's merge needs.
- **PyMuPDF (AGPL)** stays. It is good, and the swap would have cost 4 days for zero CER.
- **Perseus / CLTK / Wiktionary (CC-BY-SA)** bundle freely. A8's lexicon goes from
  ~50 words to ~2.5M forms with no sourcing gymnastics.
- **Anything else** — ocrmypdf, ghostscript, any research model on any licence.

*The one durable finding from the parked analysis:* the CC-BY-4.0 Kraken models in
`models/manifest.json` require attribution, which is currently missing. A `NOTICE` file
is ten minutes and it is simply correct. Not a blocker, not a workstream.

### 2.2 CPU-only — reopened

**Stated assumption, flagging it because it is load-bearing:** ARCHITECTURE.md locks
CPU-only, and that lock existed to serve the desktop/local-first target. You have
dropped local-first. I am therefore treating **CPU-only as dropped too**, and A7 depends
on it. If you want CPU-only kept for a reason unrelated to local-first, say so and A7
shrinks to Calamari plus PaddleOCR.

What GPU buys, in rough order of CER impact:

- **Fine-tuning at usable speed.** A ketos run that takes 10 hours on CPU takes ~25
  minutes on a GPU. This converts A2 from a once-per-project ordeal into a per-book
  routine, which is the difference between one fine-tune and twenty.
- **Transformer recognizers** — TrOCR and successors, which outperform CTC/LSTM models
  on degraded and ligature-heavy print, and are impractical on CPU.
- **Local VLMs** — GOT-OCR2, olmOCR, Qwen2.5-VL running on your hardware. Removes the
  API-cost ceiling on A9, so arbitration can be generous instead of rationed.
- **Higher render DPI and super-resolution** on the hard varieties.

The 54× VLM latency penalty reported in arXiv 2510.10138 is a *throughput* finding.
When accuracy is the only objective, it stops being an argument.

---

## 3. Baseline — what exists

Verified by reading the tree. 64 modules, ~8,200 LOC, 56 test files, clean hexagonal
layering that is actually enforced.

**Working:** PyMuPDF ingest · grayscale + Sauvola preprocess · Kraken/Tesseract layout
with fallback · Tesseract + Kraken + Calamari + VLM recognition · confidence and
script-aware reconciliation · suggest-only post-correction · paragraph/hyphenation/
running-head structure · six exporters · Streamlit review UI.

**Built and never run — the important finding.** The entire fine-tuning loop is complete:

```
ICorrectionStore → TrainingSample → alto_training.py → KetosTrainer (ITrainer)
    → ModelCandidate → BeatsParentOnHeldOut + refuse_unless_test_split
    → PromotedModel → IModelRegistry → ScriptRouter._PromotedResolver
    → _build_engine_on_checkpoint
```

`TrainingOrchestrator.run_training` (238 LOC), `promotion.py` (142 LOC),
`ketos_trainer.py` (155 LOC), MLflow registry, `promoted_for_typeface` on the port,
router resolution with checkpoint caching. Tested. Wired. **Never executed, because
there is no ground truth to train on.**

This reframes A1 completely. The corpus is not test data — it is *fuel* for the single
largest accuracy lever available, and the machinery to burn it is already built.

**Thin:** `preprocess.py` is 89 lines — grayscale and Sauvola. No deskew, no dewarp, no
denoise, no despeckle. On scanned books this is where free CER lives.

**Unmeasured:** everything except one page of one book.

---

## 4. Ranking by CER impact

Reordered from the earlier plan. The earlier ordering optimized value-per-effort;
this one optimizes CER, full stop.

| # | Workstream | Expected CER impact | Days |
|---|---|---|---|
| **A1** | Ground-truth corpus (measurement **and** training fuel) | Enables everything. Zero alone. | 6 |
| **A2** | **Fine-tune on target material** | **0.126 → 0.005–0.015.** Largest single lever by a wide margin — and the gap is wider than first estimated. | 5 |
| **A3** | Preprocessing: deskew, dewarp, denoise + variant ensembling | **Measured, mixed.** Despeckle: 14.8% relative, shipped. Deskew: worse on square pages, not default. Variant ensembling: **every configuration worse than baseline** on the current corpus — see docs/ENGINE_ACCURACY.md. | 5 |
| **A4** | Word-level alignment merge | 5–15% relative; scales with engine count | 3 |
| **A5** | Agreement tiers | 0 alone — enables A9 and review triage | 2 |
| **A6** | Per-document model bake-off | Prevents 7× regressions; large on mismatched material | 4 |
| **A7** | Engine expansion (Calamari, PaddleOCR, TrOCR, local VLM) | 10–20% relative via decorrelated errors | 6 |
| **A8** | Real lexicon + confusion model | **Prerequisite, not a nice-to-have.** Polytonic/ancient/modern currently have **no lexicon at all** (only byzantine 59 words and pontian 58), so A6's 0.4 lexicon term is identically zero on the primary script and post-correction highlights nothing there. Small on raw CER; large on *post-review* accuracy; unblocks two shipped features. | 5 |
| **A9** | VLM arbitration + structure-only layout | 5–10% relative on contested lines | 5 |
| **A10** | Apparatus criticus zoning | Large on critical editions, zero elsewhere | 4 |

**Total ~45 days.** A1 and A2 are 11 of those and deliver more than the other 34
combined. If you do nothing else, do those two.

---

## 5. Workstreams

Each states **why**, the **paradigm** and **pattern** (matching the existing
functional-core / imperative-shell architecture), the **surface**, **tests**, and an
objective **DoD**.

---

### A1 — Ground-truth corpus

**Why.** Two jobs, both blocking. It is the only way to measure any change, and it is
the training data for A2. Every downstream number is currently n=1.

**Paradigm.** Data. Resist building tooling around it; the work is transcription.

**Pattern.** *Repository* — `ICorpusRepository.pages(split, script)` already exists with
train/dev/test splits and a `refuse_unless_test_split` promotion guard. Extend the
on-disk layout, invent nothing.

**Size — larger than the earlier plan, because it now feeds training.** Measurement
needs ~20 pages. Fine-tuning wants **50–100 pages per typeface family** to beat a good
parent model. Split the difference by weighting toward the varieties you will fine-tune:

| Variety | Pages | Purpose |
|---|---|---|
| Polytonic 20th c. Didot | 30 | Fine-tune target + continuity with the 0.038 baseline |
| 19th c. German serif | 30 | Fine-tune target; two bundled models suit this face |
| Critical edition | 20 | Fine-tune target + the only A10 material |
| Byzantine / ligature | 15 | Hardest variety; needs the most training signal |
| Modern monotonic | 10 | Baseline sanity |
| Pontian | 10 | Post-correction path |

**115 pages.** That is 3–4 weeks of transcription, and it is the honest price of the
0.005–0.015 target. A 20-page version measures but does not train; be clear which you
are buying.

```
corpus/ground-truth/
  manifest.json                provenance, DPI, variety, typeface family per page
  <doc-id>/p<NNN>.png          400 DPI
  <doc-id>/p<NNN>.gt.txt       diplomatic, NFC, one line per printed line
  <doc-id>/p<NNN>.meta.json    variety, typeface, region map
infrastructure/corpus_repository.py   change  expose typeface family; enforce split disjointness
```

**Transcription rules.** Diplomatic — reproduce what is printed including typos; NFC;
line breaks as printed; `[?]` for illegible; no abbreviation expansion; no hyphen
joining. Second reader spot-checks 20%; disagreements logged, not silently resolved.
**Splits must be disjoint by document, not by page** — pages from one book in both train
and test leaks typeface and inflates every fine-tuning result you will produce.

**Tests.** Manifest parses; every declared page exists; every `.gt.txt` is valid NFC
UTF-8; splits disjoint **by document**; per-variety page counts meet the table.

**DoD.** 115 pages across 6 varieties, document-disjoint splits, `ENGINE_ACCURACY.md`
rewritten with per-variety tables and n stated in every claim.

**Effort.** 6 days engineering-adjacent; transcription runs in parallel from day 1.

---

### A2 — Fine-tune on target material

**Why.** The largest single lever in applied OCR, and your infrastructure for it is
already written and tested. An off-the-shelf model is trained on *someone else's* books;
a model fine-tuned on 50 pages of *this* typeface routinely halves CER or better. Your
own manifest already demonstrates the principle in miniature — three models on identical
input spanning 0.038 to 0.264 purely on training-data match.

**Paradigm.** Imperative orchestration over pure policy. Already correct in
`training_orchestrator.py`: the trainer does I/O, `promotion.decide` is a pure function
of two evaluation reports.

**Pattern.** *Strategy* (`ITrainer`) + *Policy Object* (`BeatsParentOnHeldOut`) +
*Registry* (`IModelRegistry`). All present. This workstream is **running** the loop,
not building it.

**Surface — small, because the machinery exists.**

```
interfaces/cli.py                  add  omniocr train / promote / models subcommands
application/training_orchestrator.py  change  per-typeface runs, not per-script only
infrastructure/ketos_trainer.py    change  expose epochs, LR, augmentation, early stop
infrastructure/model_manifest.py   change  record fine-tuned models with parent lineage
models/manifest.json               grow    one entry per promoted fine-tune
```

**Procedure per typeface family:**

1. Train split → ALTO via the existing `alto_training.py` exporter.
2. `ketos train --load <best parent from A6> --resume` — fine-tune, never train from
   scratch. Parent selection matters: fine-tuning the 0.264 model wastes the run.
3. Augmentation on: elastic distortion, blur, noise, contrast jitter. Scanned books vary
   more than any single training set; augmentation is close to free CER.
4. Early-stop on dev-split CER.
5. `BeatsParentOnHeldOut` on the **test** split, guarded by `refuse_unless_test_split`.
   A candidate that does not beat its parent is discarded, not shipped.
6. Promote → registry → router resolves it via `promoted_for_typeface`.

**The compounding loop, which is the real prize.** `ICorrectionStore` already captures
human-accepted corrections from the review UI as `Correction` objects, and
`alto_training.py` already turns them into training samples. So: every book a user
corrects becomes training data for the next book in the same typeface. Close this loop
and accuracy improves with use rather than staying fixed at release. **This is the
single most valuable property the system can have**, and it is roughly 20 lines of
wiring plus a scheduled retrain.

**Tests.** A fine-tune on the train split beats its parent on the test split, per
variety. Promotion refuses a candidate evaluated on train or dev. Registry lineage
records parent hash. A correction round-trips store → sample → ALTO → ketos.

**DoD.** One promoted fine-tune per typeface family. Per-variety CER before/after in
`ENGINE_ACCURACY.md`. Polytonic Didot **below 0.015**. Correction→retrain loop closed
and demonstrated end to end on one book.

**Effort.** 5 days. Hard-depends on A1.

---

### A3 — Preprocessing: correction and variant ensembling

**Why.** `preprocess.py` is 89 lines: grayscale and Sauvola. Real scanned books arrive
skewed 0.5–3°, page-curved near the gutter, speckled, and unevenly lit. Every one of
those costs CER before the recognizer sees a pixel, and none is currently corrected.
This is the cheapest unclaimed accuracy in the codebase.

**Paradigm.** Pure image transforms behind the existing `IImageProcessor` port. Each
transform is a function of bytes to bytes with no state.

**Pattern.** *Decorator/Pipeline* for composing transforms — `IImageProcessor` already
composes — plus *Composite* for the variant fan-out.

**Two halves.**

**(a) Correction — deterministic, always on.**

| Stage | Method | Why it matters |
|---|---|---|
| Deskew | Radon / Hough on the binarized page | Kraken's line segmentation degrades sharply past ~1°. Highest-value item here. |
| Dewarp | Page-curl estimation from text-baseline curvature | Gutter-side lines on thick books; large on library scans. |
| Denoise | Non-local means, or median for salt-and-pepper | Speckle becomes phantom diacritics — a specifically Greek failure. |
| Despeckle | Connected-component area filter | Removes marks smaller than a tonos without touching real ones. |
| Illumination | Background subtraction before binarization | Uneven lighting breaks global thresholds on photographed pages. |
| Binarization | Sauvola (exists) **and** Otsu **and** grayscale passthrough | Different recognizers prefer different inputs. Do not pick one. |

**(b) Variant ensembling — the part that only makes sense now.**

The same model on the same line, given differently preprocessed images, makes
*different* errors. Feed N preprocessing variants into A4's merge and you get ensemble
benefit from one model. When compute is not a constraint this is nearly free CER:

```
variants = [grayscale, sauvola, otsu, denoised+sauvola, 1.5×upscale+sauvola]
→ 5 recognitions per line → A4 alignment merge → A5 agreement tier
```

5× compute for 10–30% relative CER on degraded material. Under the old CPU-only
constraint this was unaffordable; it is now one of the better trades available.

**Measured 2026-09-05 and it did not hold.** Through the real per-line pipeline
on the three scan fixtures, every variant count scored *worse* than the
single-variant baseline (0.1226 CER): 2 variants 0.1283, 3 variants 0.1306,
4 variants 0.1277. Two variants cannot help at all — a 1–1 tie goes to the
pivot, so the merge is a no-op. The extra candidates also degrade the
*chosen* line, because the reconciler falls back to confidence and Tesseract
is confidently wrong on binarized input. This corpus is clean print, which is
not the material the technique targets; the estimate above should be treated
as unvalidated until a degraded variety exists to test it on.

```
infrastructure/preprocess.py   grow   Deskew, Dewarp, Denoise, Despeckle,
                                      Illumination, OtsuProcessor  (~250 LOC)
application/pipeline.py        change fan out over variants before routing
domain/models.py               add    preprocessing variant id on EngineRun provenance
```

**Provenance.** `EngineRun` must record which variant produced a reading, or A4's merge
becomes untraceable and faithfulness breaks. One field.

**Tests.** Synthetic skew of known angle is corrected to within 0.1°. Denoise does not
remove marks the size of a tonos — property-tested, since that is the failure that would
silently destroy polytonic accuracy. Variant fan-out produces N candidates with distinct
provenance. Measured on A1: per-variety CER for each variant and for the ensemble.

**DoD.** Deskew and denoise always on. Variant ensembling behind `--variants N`.
Per-variety before/after published. No variant configuration is worse than the current
grayscale baseline.

**Effort.** 5 days.

---

### A4 — Word-level alignment merge

**Why.** Both current reconcilers pick **one whole line** and discard the rest. With
A3's variants and A7's engines you will have 10–15 candidates per line; discarding 14 of
them is indefensible. A line where Kraken is right on the Greek and Tesseract on the
Latin footnote marker currently loses half its correct output.

**Paradigm.** Pure functional, strictly. Deterministic transform over token sequences —
no I/O, no state. The most testable module in the plan.

**Pattern.** *Value Object* (`AlignedToken`) + *Strategy* (`AlignedReconciler`
implements the existing `IReconciler`, so it is a one-line swap at any composition root).

**Implementation.** `difflib.SequenceMatcher.get_opcodes()` — stdlib, no dependency, and
it *is* the alignment. Hand-rolling Needleman–Wunsch reproduces it in 120 lines with its
own correctness burden.

With many candidates, align pairwise against the highest-confidence **pivot** rather
than attempting true multiple-sequence alignment: O(k·n²) instead of exponential, and
the pivot is the reading most likely right. Deliberate ceiling; note it in the docstring.

**Voting.** Once aligned, each token position has k readings. Majority vote, weighted by
engine reliability measured **per variety on the A1 dev split** — not by self-reported
confidence, which `ScriptAwareReconciler`'s own docstring documents as untrustworthy on
mixed-script lines. Learn the weights; do not guess them.

**Faithfulness.** A merged line is a synthesis no engine produced. So: `OCRLine.text`
stays the pivot's real reading; the merge is emitted as a `Suggestion` with
`reason="alignment_merge"` carrying per-token provenance, so a reviewer sees which
engine and which variant supplied each token. Every exported character stays traceable.

```
application/alignment.py   new   align(), AlignedToken, vote()   ~180 LOC pure
application/reconcile.py   add   AlignedReconciler
domain/models.py           add   AlignedToken (frozen, slots)
```

**Tests.** Identical candidates → all agreed. One-token divergence isolates exactly one
disagreement. Order-stable under candidate permutation given a fixed pivot (Hypothesis —
already a dependency). Token cap at 512 enforced. `test_faithfulness.py` extended: never
emits a token no engine produced.

**DoD.** Wired into the ensemble. Aggregate CER strictly better than
`ScriptAwareReconciler` on the A1 test split with ≥ 3 candidates.

**Effort.** 3 days. Depends on A3 or A7 for candidate count to matter.

---

### A5 — Agreement tiers

**Why.** Once A4 knows which tokens disagree, that signal ranks the review queue, targets
A9's arbitration, and is the honest confidence number — far better than an engine's
self-report.

**Paradigm.** Immutable value data; classification is a pure function of alignment output.

**Pattern.** *Value Object* — `str`-backed `Enum`, matching `Script` and `RegionType`.

```python
class AgreementTier(str, Enum):
    UNANIMOUS = "unanimous"   # all candidates identical after NFC
    MAJORITY  = "majority"    # ≥2 agree, ≥1 differs
    SPLIT     = "split"       # no two agree
    SINGLE    = "single"      # one candidate only
    UNKNOWN   = "unknown"
```

One additive field on `OCRLine` with a default, so every construction site keeps
compiling and `slots=True` is preserved. Surfaced in the review queue ordering and as an
attribute in ALTO and PAGE — archival consumers should see how contested a line was.

**Never gate export on tier.** A `SPLIT` line exports its text, flagged. Silently
dropping low-agreement lines is a faithfulness violation dressed as quality control.

**Tests.** Every tier reachable. Export text byte-identical with and without tiers.
Review queue orders `SPLIT` first. Tier distribution across A1 reported — it is also a
useful diagnostic of whether your engine pool is actually decorrelated.

**DoD.** Populated on every ensemble line, visible in review, present in archival exports.

**Effort.** 2 days. Depends on A4.

---

### A6 — Per-document model bake-off

**Why.** Three bundled models span **0.038 → 0.264 CER** — a 7× spread — and selection
is a fixed manifest default. A book in a Porson face silently gets a model measured on
Didot. After A2 you will have a dozen fine-tuned models and this gets worse, not better:
more models means more ways to pick wrong.

**Paradigm.** Pure scoring function + imperative probe. The probe runs engines; scoring
is a pure fold, testable without Kraken.

**Pattern.** *Strategy* (`IModelSelector`) with `ManifestDefaultSelector` as the fallback
and `BakeOffSelector` as the new behaviour.

**Algorithm.** Sample 5 lines from the middle of the first page (a title page is not
representative of body typeface) → run every candidate model → score → select → emit a
`PipelineEvent` naming the winner, the runners-up, and their scores.

Scoring, with no ground truth available at run time:

```
score = 0.5 · mean_confidence_normalized
      + 0.4 · lexicon_hit_rate
      + 0.1 · (1 − diacritic_violation_rate)
```

The lexicon term is why **A8 raises A6's ceiling**: against ~50 words the hit rate is
noise and the score collapses to confidence alone. Ship these weights, then re-tune
against A1 once A8 lands, and record the tuned weights in an ADR.

**Better, once A1 exists — typeface classification.** Rather than probing blind, train a
small classifier on the corpus mapping page image → typeface family, then look up the
promoted model via `IModelRegistry.promoted_for_typeface`, **which is already on the
port**. Faster and more accurate than probing. Probing remains the fallback for
unrecognized faces.

```
ports/interfaces.py             add  IModelSelector
application/model_selection.py  new  ManifestDefaultSelector, BakeOffSelector,
                                     TypefaceClassifierSelector, score_candidate  ~200 LOC
interfaces/cli.py               add  --model-select {default,bakeoff,classify}
```

**Tests.** `score_candidate` pure and monotone in each term. Timed-out model excluded,
not fatal. Ties fall back to the manifest default. Selection event carries model name
and hash. On A1: selection never worse than the manifest default per variety, strictly
better on German-serif pages.

**DoD.** Default path for documents ≥ 5 pages. Per-variety CER published for all three
selectors. Choice auditable from logs alone.

**Effort.** 4 days.

---

### A7 — Engine expansion

**Why.** Ensemble accuracy is driven by **error decorrelation**, not by any member's
solo score. Two engines that fail on the same glyphs add nothing. The current pool is
Tesseract (LSTM) + Kraken (LSTM) — architecturally similar, so their errors correlate
more than they should. Adding *architecturally different* recognizers is what makes A4's
merge pay.

**Paradigm.** Imperative adapters behind `IOCREngine`, each returning `Result`. GPL and
heavyweight engines stay subprocess-isolated for **crash containment** — a segfaulting
recognizer must not take the pipeline with it.

**Pattern.** *Adapter* per engine, *Decorator* (`RetryingEngine`, exists) for transient
failure.

| Engine | Architecture | Adds | Status |
|---|---|---|---|
| **Calamari** | CTC ensemble, voting-native | Decorrelated from Kraken; built for exactly this | Adapter **exists**, re-enable by default |
| **PaddleOCR** | DB detection + CRNN | Strongest detection in the pool; better line boxes feed everything downstream | New adapter, ~150 LOC |
| **TrOCR / transformer** | Encoder-decoder, attention | Fundamentally different failure modes from CTC. Strong on degraded and ligature-heavy print — the Byzantine case. **GPU** | New adapter, ~200 LOC |
| **GOT-OCR2 / olmOCR** | Local VLM | Full-page understanding, no API cost, no egress limit. Feeds A9 generously. **GPU** | New adapter, ~200 LOC |
| **EasyOCR** | CRNN | Marginal — correlates with Tesseract, and the arXiv paper measures F1 0.0 on table structure from spatial loss | **Skip** |

**Weighted voting, learned per variety.** With 5+ engines × 5 preprocessing variants,
naive majority vote is wrong — a bad engine votes as loudly as a good one. Fit
per-engine, per-variety reliability weights on the A1 **dev** split and feed them to A4's
`vote()`. Never fit on test.

**Cost.** 25 recognitions per line at maximum fan-out. Irrelevant to the stated goal;
gate it behind `--thorough` so a fast path still exists for iteration.

**Tests.** Each adapter returns `Result` and never raises across a layer. Subprocess
engines are killed on timeout without orphaning. Decorrelation measured: pairwise error
overlap per engine pair on A1, reported — an engine that adds no decorrelation is
removed, not kept for completeness.

**DoD.** ≥ 4 engines in the ensemble. Pairwise decorrelation table published. Aggregate
CER with the full pool strictly better than with Tesseract + Kraken alone.

**Effort.** 6 days. TrOCR and local-VLM adapters assume §2.2's GPU assumption holds.

---

### A8 — Lexicon and confusion model

**Why.** `lexicons.py` ships ~50 hardcoded words. That vocabulary carries every
unknown-word flag, every diacritic check, and 40% of A6's scoring weight. Raw-CER impact
is modest — this stage never rewrites text — but *post-review* accuracy is what a reader
actually receives, and there it is decisive.

**Paradigm.** Data-oriented for the lexicon; pure functional for candidate generation.

**Pattern.** *Repository* behind the existing `ILexicon` (`contains(token)` — the whole
interface, unchanged) + *Strategy* for the confusion check inside `SuggestOnlyCorrector`.

**(a) Lexicon.** Licences no longer constrain sourcing, so take everything:
Perseus/CLTK ancient forms (~1.5M), Hunspell `el_GR` expanded (~800k), Greek Wiktionary
(~200k), plus the existing curated Byzantine and Pontian tuples layered **above** the
bulk lists so a generic corrector cannot normalize legitimate historical or regional
forms into standard Greek — the entire point of that module's docstring.

Storage: one sorted newline-joined UTF-8 blob per lexicon, memory-mapped, `bisect` for
lookup. ~20 MB on disk, near-zero resident, O(log n), stdlib only. A `frozenset` of 2.5M
forms costs ~250 MB resident for no benefit.

**(b) Confusion model** — the substance of what Rigaudon contributed. A lexicon miss
currently yields a bare `unknown` flag with no candidate; the reviewer gets a problem,
not a proposal.

Generate edit-distance-1 variants restricted to an **OCR-plausible** substitution table,
keep those the lexicon contains, rank by lexicon layer then class frequency, **emit at
most 3**. A reviewer shown ten candidates stops reading them.

| Class | Examples | Cause |
|---|---|---|
| Iotacism | ει ↔ ι ↔ η ↔ υ ↔ οι | Training-data conflation of homophones |
| Breathing | ἀ ↔ ἁ, ᾿ ↔ ῾ | Sub-pixel marks |
| Accent | ά ↔ ὰ ↔ ᾶ | Same |
| Sigma | σ ↔ ς ↔ c | Final-form and lunate |
| Latin homoglyph | ο↔o, ν↔v, ρ↔p, Α↔A | Mixed-script pages; a live instance is documented in `reconcile.py` |
| Ligature split | ϗ → καί, ου-ligature | Byzantine faces |
| Glyph shape | ν ↔ υ, θ ↔ ϑ, γ ↔ ν | Visual similarity |

**No regex** — the table is a `Mapping[str, tuple[str, ...]]` and generation is a bounded
loop. Cap token length at 64 and candidate count before ranking, so a noise-page token
cannot drive combinatorial expansion. All output is `Suggestion`; rule 1 holds.

```
infrastructure/lexicons.py   change  SortedBlobLexicon; keep curated layer above bulk
scripts/build_lexicon.py     new     fetch → normalize NFC → dedupe → sort → hash
application/confusion.py     new     CONFUSIONS table, candidates(), rank()  ~150 LOC pure
application/post_correction.py change add the confusion check
```

**Tests.** Blob lookup matches a reference set. Resident memory under a stated ceiling
for 2.5M forms. Known OCR errors from A1 produce the correct reading in the top 3.
A correct token produces no candidates. Generation bounded for a 64-char noise token.
**A Pontian form absent from the bulk lexicon is not "corrected" into standard Greek** —
the regression that would make this feature actively harmful.

**DoD.** ≥ 2.5M forms loadable. Unknown-flag rate on A1 before/after. Proportion of
flags gaining a correct top-3 candidate, published.

**Effort.** 5 days.

---

### A9 — VLM arbitration, and structure-only layout

**Why.** The VLM currently runs on **every line** of polytonic/ancient/Byzantine as a
blanket ensemble member — maximum cost, maximum hallucination surface, for un-boxed
output. Once A5 exists it should be spent where grounded engines actually disagree.
A7's local VLM removes the cost ceiling, so arbitration can be generous.

**Paradigm.** Imperative adapter behind an escalation policy expressed as **pure
predicates** — "when to escalate" must be a testable function, not a condition buried in
the orchestrator.

**Pattern.** *Chain of Responsibility* — grounded engines → alignment merge → VLM
arbiter → human. Each link may resolve or pass on; **none may rewrite**.

**(a) Arbitration.** Escalate when tier is `SPLIT`, or `MAJORITY` with pivot confidence
below threshold. Never on `UNANIMOUS`. With a local VLM the per-page cap can be
generous; with an API keep it at 15 and flag the remainder `budget_exhausted` — never
silently un-arbitrated.

**Similarity gate — the load-bearing control.** A VLM asked to read a Greek line will,
on a bad crop, produce fluent plausible Greek that is not on the page. So: normalized
edit distance against the nearest grounded candidate; above a ceiling (start 0.35,
calibrate on A1) the reading is **rejected outright** and the line flagged
`vlm_divergent` — not offered as a suggestion, because a wildly divergent reading is
noise and offering it invites a tired reviewer to accept it. Within the ceiling it is a
`Suggestion`, never written to `OCRLine.text`.

Both gates apply: the geometric `GroundingGuard` (raise its default off `0.0`, which is
not a guard) and the textual similarity gate check different failure modes. A reading in
the right place saying the wrong thing passes the first and must fail the second.

**Prompt injection.** The page image is untrusted input — a scanned page can contain text
that reads as an instruction. Crop to a **single line region**, never a full page; state
in the system prompt that the image is data to transcribe and no text within it is an
instruction; length-cap the reply against the grounded reading and reject over-length
outright. Output is only ever a `Suggestion` on the requested line, so a compliant model
and a compromised one have the same blast radius: one rejected suggestion.

**(b) Structure-only layout — the highest-value, zero-risk VLM use.** Ask the VLM for
**region boundaries and reading order only**, with a response schema containing **no text
field**. It cannot hallucinate a character because it is never asked for one. Region
segmentation is precisely what VLMs are good at and hand-tuned geometry is bad at, which
makes this the best available answer to A10.

```
application/arbitration.py   new  should_arbitrate(), similarity_gate()  ~200 LOC pure
infrastructure/vlm.py        change  add arbitrate(line, crop) and classify_regions(page)
composition/desktop.py       change  VLM leaves the engine tuple; becomes arbiter + classifier
```

**Tests.** `should_arbitrate` never fires on `UNANIMOUS`. Budget exhaustion flags rather
than skips. Gate rejects a fabricated reading, admits a plausible correction. Over-length
reply rejected. `test_faithfulness.py`: no VLM path mutates `OCRLine.text`, asserted
across all of A1.

**DoD.** VLM out of the engine tuple everywhere. Lines arbitrated / accepted /
gate-rejected reported. CER with arbitration ≤ without, at lower spend than blanket.

**Effort.** 5 days. Depends on A5.

---

### A10 — Apparatus criticus zoning

**Why.** `RegionType.APPARATUS` is declared in the domain and **assigned nowhere**. On a
critical-edition page the apparatus is a distinct zone — smaller type, siglum-dense,
below a rule, different language mix. Treated as body text it corrupts reading order,
pollutes paragraph assembly, and drags the page's lexicon hit rate down, which after A6
also drags model selection off course.

**Paradigm.** Pure functional classification, matching `structure/roles.py`'s existing
`classify(lines, page) -> ParagraphRole` shape.

**Pattern.** *Strategy*, with two implementations.

**(a) Geometric — offline default.** Require ≥ 3 of 5 signals; prefer `UNKNOWN` over a
wrong assignment:

1. Line height < 0.75 × page median — the strongest signal
2. Bottom third of the text block
3. Separator rule above, detectable in the binarized image A3 already produces
4. Siglum density — single Latin capitals, superscript numerals, `cod.` `om.` `add.`
   `secl.` `coni.`
5. Line-length variance — apparatus is ragged, body is justified

**(b) VLM region classifier — A9(b).** Better, and now available. Geometric stays the
fallback.

Scholia and marginalia use the same machinery with different geometry; `SCHOLIA` and
`MARGIN` are already in the enum.

**Faithfulness.** Zoning is metadata. Apparatus text is recognized, exported, never
dropped. Reading-order changes are recorded in `OCRLine.reading_order`, already a field —
reversible by construction.

```
application/structure/regions.py   new  classify_region()  ~120 LOC pure
application/layout.py              change  assign region_type after segmentation
application/structure/assembler.py change  apparatus forms its own blocks, ordered after main
infrastructure/exporters.py        change  region_type on ALTO TextBlock / PAGE TextRegion
```

**Tests.** A1's critical-edition pages classify correctly. **Zero false positives on
non-critical pages** — the more important direction. A footnoted page classifies
`FOOTNOTE`, not `APPARATUS`. Region type present in both archival exports with text
byte-identical.

**DoD.** `APPARATUS` assigned on A1 critical-edition pages, zero false positives
elsewhere, in ALTO and PAGE, DOCX renders it in a distinct style.

**Effort.** 4 days.

---

## 6. Sequencing

```
Phase 0 — FUEL                          Phase 1 — THE LEVER
A1 corpus, 115 pages          ────────► A2 fine-tune per typeface
6d + parallel transcription             5d
                                        ── target: 0.038 → <0.015 ──

Phase 2 — ENSEMBLE                      Phase 3 — SELECTION
A3 preprocess + variants ──┐            A6 bake-off / typeface classify
A7 engine expansion      ──┼──► A4 merge ──► A5 tiers        4d
5d + 6d                    │    3d           2d
                           └──────────────────────┘

Phase 4 — CORRECTION                    Phase 5 — TARGETED
A8 lexicon + confusion                  A9 arbiter + structure-VLM ──► A10 apparatus
5d                                      5d                            4d
```

| Phase | Days | Cumulative CER story |
|---|---|---|
| 0 — A1 | 6 | Everything becomes measurable. No CER change. |
| 1 — A2 | 5 | **The big one.** Per-typeface fine-tunes. |
| 2 — A3, A7, A4, A5 | 16 | Decorrelated pool + variant fan-out + word-level merge. |
| 3 — A6 | 4 | Right model per document; prevents 7× regressions. |
| 4 — A8 | 5 | Post-review accuracy; also lifts A6's scoring. |
| 5 — A9, A10 | 9 | Contested lines and critical-edition structure. |

**~45 days.** A1 + A2 is 11 days and delivers more than the remaining 34.

**Ship-and-measure, enforced.** Every workstream lands behind a flag where one is
natural, with before/after per-variety CER on the A1 **test** split recorded in
`ENGINE_ACCURACY.md`. A workstream that does not move a measured number is reverted, not
defended. This is the only discipline that keeps a 45-day accuracy plan honest.

---

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **A1 transcription slips** — 115 pages is 3–4 weeks | Blocks A2, which is the whole plan | Start day 1, before any code. Stage it: 20 pages unblocks measurement, the rest unblocks training. Weight early pages toward the two fine-tune targets. |
| **Train/test leakage inflates every result** | Fatal and invisible — you would ship believing a false number | Splits disjoint **by document**, not by page. Asserted in `test_corpus.py`. `refuse_unless_test_split` already guards promotion. |
| **Fine-tune overfits 30 pages** | A2 underdelivers | Augmentation on; early-stop on dev; `BeatsParentOnHeldOut` on test. A candidate that does not beat its parent is discarded. |
| **Engines correlate, ensemble adds nothing** | A4 and A7 wasted | Measure pairwise error overlap on A1 and publish it. Drop members that add no decorrelation rather than keeping them for the count. |
| **Overfitting the corpus itself** | The number improves, real books do not | Hold one variety entirely out of tuning as a true blind set. Never tune a threshold on test. |
| **Variant fan-out slows iteration to uselessness** | Development stalls | `--thorough` gates full fan-out; a 1-variant fast path stays for the edit-test loop. |
| **VLM hallucination enters the corpus** | Worst possible outcome for scholarly output | Suggestion-only, both gates, line-crop only, length cap. Blast radius: one rejected suggestion. |
| **Scope drift over 45 days** | The usual | Objective DoD per workstream. Anything not in a DoD is a separate proposal. |

---

## 8. Definition of done

1. Per-variety CER on the A1 **test** split meets the §1 targets, or the miss is
   published with an explanation.
2. `omniocr eval --corpus corpus/ground-truth --split test` reproduces every published
   number from a clean checkout.
3. `ENGINE_ACCURACY.md` reports per-variety CER over 115 pages, with n in every claim and
   a before/after column per workstream.
4. At least one promoted fine-tune per typeface family, with parent lineage recorded.
5. The correction → retrain loop is closed and demonstrated end to end on one book.
6. Pairwise engine decorrelation published; every ensemble member justified by it.
7. `test_faithfulness.py` passes across the whole corpus: no stage — preprocessing,
   merge, arbitration, confusion, zoning — mutates recognized text.
8. Train/test splits proven document-disjoint by an automated test.
9. `mypy --strict` clean, coverage ≥ 80% overall and ≥ 90% on new modules.
10. Every threshold introduced here — similarity 0.35, grounding, bake-off weights,
    apparatus 3-of-5, vote weights — calibrated on **dev**, never test, and recorded in
    an ADR with the measurement that chose it.
