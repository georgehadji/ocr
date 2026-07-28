# OmniOCR v2 — Plan

Successor to [BUILD_PLAN.md](BUILD_PLAN.md) (v1 phases 0–6). Design of record remains
[ARCHITECTURE.md](ARCHITECTURE.md).

v1 built the whole structure. v2 makes it **true**.

---

## 1. Where v1 actually landed

Honest state, verified by execution on 2026-07-28 — not by reading the audit report.

**Real and proven:**

- Clean hexagonal architecture, 37 source files, 6 layers, no layer violations.
- 165 tests, 163 passing. `ruff`, `ruff format`, `mypy --strict`, license isolation all green.
- **Tesseract accuracy measured and gated** — real CER on rendered Greek fixtures
  (polytonic 0.0000, ancient 0.0076, byzantine 0.0118, modern 0.0135), regression-gated,
  proven non-vacuous.
- **Faithfulness proven end-to-end** — recognized text reaches TXT/Markdown/ALTO/PAGE-XML
  byte-identical, including polytonic diacritics, the `ϗ` ligature, and Pontian vocabulary.

**Written but never executed against reality:**

- **Kraken has zero accuracy coverage.** No `.mlmodel` is committed, so the comparison test
  skips. ARCHITECTURE.md §2 designates Kraken — not Tesseract — as the accuracy driver for
  polytonic, ancient, and Byzantine print. The engine carrying the product's hardest
  requirement is unverified.
- **The training loop cannot learn.** See §2 — two structural defects, both invisible because
  the code was never run.
- VLM, Calamari, Server and Cloud editions: code exists, never exercised against a live
  endpoint, a real subprocess, or a real queue.

### The v1 lesson

Every defect found in review shared one cause: **code written, never executed.** A crash on
the first log call in `run()` survived a suite reporting "138 tests passing, 87% coverage,"
because no test ran the pipeline on a base install. Coverage measured which lines were
*imported*, not which behaviors were *proven*.

**v2 operating rule: no capability is "done" until it has been run against real input and the
result recorded.** Every phase below ends in an artifact — a measured number, a produced file,
a captured trace — not a passing mock.

---

## 2. Blocking defects carried into v2

Found by reading `infrastructure/training.py` against Kraken's actual training contract.
Both must be fixed before any training work; neither is cosmetic.

### D9 — Training data contract is wrong (High)

`export_ground_truth_to_kraken_json()` writes one record per line, but points every record
at the **full page image**:

```python
image_path = output / f"page-{page.number}.png"   # whole page
for line in page.lines:
    records.append({"image": image_path.name, "text": text})  # one line's text
```

Kraken's `ketos train` expects **line-level** crops paired with their transcription (or
ALTO/PAGE XML it can segment itself). Training on a full page labelled with a single line's
text teaches the model that the entire page reads as that one line. The result would be a
model measurably worse than the pretrained baseline.

**Fix:** crop each line by its `bbox` and emit line images, or emit ALTO/PAGE-XML and let
`ketos` do the segmentation. Prefer ALTO — the exporter already produces it, it round-trips
geometry, and it is the archival format ARCHITECTURE.md §6 already commits to.

### D10 — "Ground truth" is the model's own output (High)

```python
def _ground_truth_text(line, page):
    # In a real training pipeline, ground truth would come from accepted
    # suggestions. For now, use the original OCR text.
    return getattr(line, "text", "")
```

This returns the **uncorrected OCR text**, so fine-tuning would train the model on its own
predictions. That is not learning — it is a self-confirming loop that reinforces existing
errors, including the exact systematic errors on hard scripts that fine-tuning is meant to
remove.

**Fix:** ground truth must come from **accepted human corrections** in the review UI. This is
the whole premise of "learning from what it does" and it is currently not wired.

---

## 3. v2 goals

Ordered by how much each moves the product, not by implementation ease.

| # | Goal | Why it matters |
|---|---|---|
| **G1** | Kraken proven on real printed Greek | The designated accuracy driver is currently unmeasured; without it the hard varieties are unserved |
| **G2** | A training loop that genuinely improves CER | The differentiator: adapt to a specific typeface no pretrained model handles |
| **G3** | Real scanned pages, not rendered text | Rendered fixtures have no scan noise, skew, bleed-through, or broken type |
| **G4** | Byzantine manuscript HTR | Explicitly deferred from v1 (ARCHITECTURE.md §2); the largest unserved corpus |
| **G5** | Editions proven in operation | Server/Cloud/VLM/Calamari code paths never executed |

---

## 4. Workstreams

### W1 — Prove Kraken (closes v1 debt, unblocks everything)

Nothing else in v2 is meaningful until the accuracy driver is measured.

1. Select and license a Greek Kraken model (Ciaconna family for ancient/polytonic print);
   commit to `models/` with source, license, SHA-256 in the manifest.
2. Un-skip `test_kraken_beats_tesseract_on_hard_scripts`; record real Kraken CER per script
   into `engine_baselines.json` alongside Tesseract's.
3. Publish the honest comparison table. **If Kraken does not beat Tesseract on polytonic and
   ancient, ARCHITECTURE.md §2 is wrong and the routing rules must change** — that is a real
   possible outcome, not a formality.
4. Wire the script router to the measured winner per script, replacing today's assumed routing.

**Done when:** every script variety has measured CER for both engines, committed and gated,
and the router's choices are justified by those numbers.

### W2 — A real corpus (G3)

Rendered fixtures test the pipeline; they do not test OCR. Real scans have skew, noise,
bleed-through, broken and inked-over type.

1. Source public-domain scanned pages per variety — modern, polytonic, ancient critical
   edition (with apparatus), Byzantine printed, Pontian. Record provenance and licence per page.
2. Transcribe ground truth diplomatically (faithful to the page, per ARCHITECTURE.md §1).
   This is slow manual work and is the real cost of v2.
3. Split **train / dev / test**, with the test set never used for tuning.
4. Recompute all baselines against real pages; expect CER to rise sharply from today's
   near-zero synthetic numbers. **That rise is the point** — it is the first honest measurement
   of the product.

**Done when:** baselines reflect real scans, and the synthetic fixtures are demoted to
pipeline smoke tests only.

### W3 — A training loop that learns (G2, fixes D9/D10)

1. Fix **D9**: emit line-level crops or ALTO for `ketos`. Verify by training one model and
   confirming it converges rather than collapsing.
2. Fix **D10**: capture accepted corrections from the review UI as the ground-truth source;
   store corrections separately from OCR output (the existing `Suggestion` layer already keeps
   them distinct — connect it).
3. Close the loop: correct → export ground truth → fine-tune → evaluate on the **held-out test
   set** → register in MLflow with the CER delta.
4. Guard against the failure mode: refuse to promote a fine-tuned model that does not beat its
   pretrained parent on held-out data. Log the refusal.
5. Per-typeface model registry — tag a document ("Venice 1523") and route its pages to the
   model fine-tuned for it.

**Done when:** a fine-tuned model measurably beats the pretrained baseline on held-out real
pages, and the number is reproducible from a committed script.

### W4 — Byzantine manuscript HTR (G4)

Explicitly out of v1 scope. Requires W2 and W3 working first — manuscript work is training
work.

1. Extend the corpus to manuscript minuscule with diplomatic transcription.
2. Baseline segmentation on manuscript layout (dense, non-rectangular, marginalia).
3. Fine-tune a Kraken HTR model per scribe/hand rather than per typeface.
4. Abbreviation and ligature expansion for manuscript conventions — reversible and
   suggest-only, exactly as the printed layer already is.

**Done when:** a manuscript page produces a usable diplomatic transcription with review flags,
measured against held-out ground truth.

### W5 — Prove the editions (G5)

Each of these is code that has never run in anger.

1. **VLM** — exercise against a live endpoint using recorded fixtures in CI. Prove the
   grounding guard rejects real hallucinations, not just synthetic ones.
2. **Calamari** — run the real subprocess; confirm GPLv3/TensorFlow isolation holds under
   actual invocation and that voting improves CER on degraded pages.
3. **Server / Cloud** — run a real job through RQ and Celery: submit → poll → download,
   plus resume-after-kill and tenant isolation under concurrent load.
4. **Desktop** — a scholar completes a full book end-to-end: ingest, review, correct, export.

**Done when:** each edition has an executed trace, not just a unit test.

---

## 5. Sequencing

W1 gates everything — routing decisions depend on measured accuracy. W2 gates W3, because
training on rendered text teaches nothing about real scans. W4 depends on W3. W5 runs in
parallel throughout.

```
W1 Prove Kraken ──┬─→ W2 Real corpus ──→ W3 Training loop ──→ W4 Manuscript HTR
                  └─→ W5 Prove editions (parallel)
```

**Recommended first move:** W1. It is small, it closes v1 debt, and its outcome may change the
architecture. Discovering Kraken *doesn't* beat Tesseract is far cheaper to learn now than
after building a training loop on that assumption.

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Kraken underperforms Tesseract on printed Greek | ARCHITECTURE.md §2 premise is wrong; routing must be rebuilt | Measure in W1 before anything depends on it |
| Ground-truth transcription is the bottleneck | W2 stalls, blocking W3/W4 | Start small — a few pages per variety beats none; prioritize polytonic and Byzantine |
| Fine-tuned models overfit a single typeface | Worse general accuracy | Held-out test set; refuse promotion without measured improvement (W3.4) |
| CPU-only makes training impractically slow | W3/W4 stall | Training is a batch operation, not interactive — accept long runs, or relax CPU-only *for training* while keeping inference CPU-only |
| Real-scan CER is disappointing | Product looks worse than v1 claimed | It was always this; v1's numbers measured rendered text. Publish honestly |
| CI still billing-blocked | No gate enforcement on any of this | Resolve before v2 work lands (see §7) |

---

## 7. Preconditions

Two blockers must clear before v2 work begins:

1. **CI billing.** Jobs currently fail in 2s with zero steps executed: *"The job was not started
   because recent account payments have failed or your spending limit needs to be increased."*
   Until fixed, nothing is gated and v2 repeats v1's mistake at larger scale.
2. **A licensed Kraken model** in `models/` — W1 cannot start without it.

---

## 8. Definition of done for v2

v1's definition (BUILD_PLAN §1) stands, plus:

1. **Measured, not asserted** — every accuracy claim traces to a committed number produced by
   a reproducible script on held-out real data.
2. **Executed, not imported** — every engine, edition, and queue has run at least once against
   real input, with the trace recorded.
3. **Learning proven** — a fine-tuned model beats its parent on data it never saw.
4. **Faithful under training** — fine-tuning must not degrade the faithfulness guarantees
   already proven in v1; the byte-identity tests stay green throughout.
