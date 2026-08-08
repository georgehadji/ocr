# OmniOCR — Production Readiness Implementation Plan

**Document status:** Plan of record for the path from "well-architected scaffolding" to shippable product
**Date:** 2026-07-31
**Branch at time of writing:** `fix/ci-gates-and-audit-corrections` @ `60aaafb`
**Standards applied:** `docs/ARCHITECTURE.md` (design of record), `docs/BUILD_PLAN.md` §1 (definition of production-grade), `CLAUDE.md` (codebase rules)

> **Note on file placement.** `docs/implementation_plan.md` already exists (dated 2026-07-27, derived from the code review / UX audit stream). This document is written to the repository root as requested and **supersedes** it. The older document is left untouched; fold it in or delete it during Phase 0, task H-4.

---

## 1. Executive Summary

### 1.1 Verdict

OmniOCR has an **excellent skeleton and an unproven body**. The hexagonal architecture is real and clean, the faithfulness thesis is enforced structurally rather than by convention, and the CI gate stack is broad. What is missing is the part that makes an OCR product an OCR product: **evidence that it reads books correctly**.

Three findings drive this entire plan:

| # | Finding | Evidence |
|---|---|---|
| **1** | **Kraken — the documented "accuracy driver" — has never recognized a character.** | `pytest` run of the full suite: 2 failures, both `test_kraken_beats_tesseract_on_hard_scripts`, error `EngineError('Kraken extraction failed: Image  is not bi-level')` |
| **2** | **The CER regression gate measures nothing about real OCR.** | `tests/corpus/*.png` are PIL renderings of Arial text produced by `scripts/generate_fixtures.py`. `engine_baselines.json` records polytonic CER **0.0000** — a font round-trip, not a recognition result. |
| **3** | **The pipeline has never processed the real target document.** | `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf` (339 MB) is committed but unprocessed. Streaming ingest, bounded memory, and checkpoint-resume are unvalidated claims. |

Everything else — packaging, licensing, observability, duplicate trees — is real work but it is *release engineering*. Findings 1–3 are *product existence*.

### 1.2 Delivery shape

Four phases, each independently shippable, each ending at a gate that cannot be argued past:

| Phase | Theme | Gate | Est. |
|---|---|---|---|
| **P0** | Make the primary recognizer work | Kraken beats Tesseract CER on polytonic + ancient, measured | 3–5 d |
| **P1** | Make measurement honest | Real scanned corpus, recomputed baselines, real-document E2E run | 5–8 d |
| **P2** | Make it a product | CLI entry point, packaging, licence/release hygiene, tree consolidation | 4–6 d |
| **P3** | Close the v2 training loop + observability | Fine-tuned model measurably beats its parent, end to end | 6–10 d |

Phases are strictly ordered: P1 baselines are meaningless until P0 lands, and P3's promotion policy is untestable without P1's held-out split.

### 1.3 What this plan deliberately does not do

Per YAGNI and the locked scope in `CLAUDE.md`: no GPU edition, no manuscript HTR beyond the existing scaffold, no new abstractions without a second caller, no multi-tenant hardening beyond what the Cloud edition already carries. Scope reductions are named explicitly in §6 rather than silently dropped.

---

## 2. Current Architecture Assessment

### 2.1 Verified baseline (measured, not claimed)

| Metric | README claims | Measured |
|---|---|---|
| Tests | 136 | ~218 collected |
| Failures | 0 implied | **2** (both Kraken real-engine) |
| Coverage | 87 % | CI gate `--cov-fail-under=80`; badge stale |
| Fixture corpus | "4 fixture varieties" | 4 **synthetic** PNGs, no real scans |
| Kraken models | 3 committed, LFS-tracked, hash-pinned in `models/manifest.json` | ✅ verified correct |

The README badges are stale and currently overstate quality. They are treated as a defect (F-10).

### 2.2 Layer map and dependency direction

```
composition/  desktop.py, training.py          ← the only place concrete adapters are named
     ↓
infrastructure/  tesseract, kraken, calamari, vlm, exporters, ingest,
                 preprocess, jobs, corrections_store, ketos_trainer, …
     ↓
application/  pipeline, router, reconcile, post_correction, metrics,
              ground_truth, promotion, evaluation, corpus_split, training_orchestrator
     ↓
ports/  interfaces.py (18 protocols), lexicon.py
     ↓
domain/  models, result, errors, corrections, corpus, training   ← pure, frozen, no I/O
```

**Assessment: PASS.** No inward-pointing violation was found. `domain` imports nothing outward. `application` imports only `ports` + `domain`. The `composition/desktop.py` recognition root does not import training modules, keeping the `[training]` extra genuinely optional.

**Caveat:** `mypy` runs in CI with `--follow-imports=skip`. Strict typing therefore verifies each module in isolation but does **not** verify cross-module type agreement. The "mypy --strict clean" claim in `BUILD_PLAN` §1.2 is weaker than it reads (F-9).

### 2.3 Data flow — and the structural defect inside it

Current per-page flow in `PipelineOrchestrator._process_page`:

```
RawPage
  → IImageProcessor.process()      GrayscaleProcessor → PNG, PIL mode "L"
  → ILayoutAnalyzer.segment()      KrakenLayoutAnalyzer → binarizes internally, returns line boxes
  → for each segment:
        IRouter.route()            → engines
        IOCREngine.extract(PAGE)   ← ⚠️ receives the WHOLE PAGE, not the line
        filter blocks by bbox overlap with the segment
        IReconciler.reconcile()
        IPostCorrector.correct()   → Suggestions (source text never mutated)
  → DocumentPage
```

Two consequences, both material:

1. **Segmentation runs twice.** `KrakenLayoutAnalyzer.segment()` segments the page, then `KrakenEngine.extract()` segments *the same page again* internally. Roughly 2× the most expensive CPU stage, on a CPU-only product.
2. **The two segmentation call sites diverged, and that is the P0 bug.** `KrakenLayoutAnalyzer.segment` binarizes before calling `pageseg.segment` (`kraken.py:52–55`). `KrakenEngine.extract` does not (`kraken.py:129–130`). It receives the grayscale `L`-mode PNG from `GrayscaleProcessor` and Kraken rejects it: *"Image is not bi-level."*

The analyzer's line polygons are used only as an **overlap mask** over whole-page engine output. They never reach the recognizer. For Kraken this discards exactly the information that makes Kraken good at historical print — baseline polygons for skewed and curved lines.

**This is a port-design defect, not just a missing `if`.** `IOCREngine.extract(page)` forces every recognizer to redo layout. The fix is described in E-1.

### 2.4 Integration points

| Boundary | Adapter | Failure mode today |
|---|---|---|
| Tesseract CLI | `infrastructure/tesseract.py` | Works. Sole verified recognizer. |
| Kraken (in-process) | `infrastructure/kraken.py` | **Broken** — F-1 |
| Calamari (GPLv3) | `infrastructure/calamari.py` subprocess | Isolated; `scripts/check_license_isolation.py` gates imports ✅ |
| VLM (network) | `infrastructure/vlm.py` | Opt-in, retry + circuit breaker, grounding guard ✅ |
| PDF ingest | PyMuPDF via `infrastructure/ingest.py` | Streaming generator; unvalidated at 339 MB |
| Job state | `InMemoryJobStore` / Redis / RQ / Celery | In-memory default loses state on restart |
| Model registry | MLflow with in-memory fallback | Graceful degradation ✅ |

### 2.5 Scalability

- Parallelism is **across pages** and currently **not exercised** — `run()` and `run_iteratively()` are single-threaded loops. `BUILD_PLAN` §6 specifies `ThreadPoolExecutor`. Deferred to E-2 with a measured justification requirement.
- Checkpointing writes the **entire accumulated `DocumentStructure`** on every page (`pipeline.py:247–252`). At page 1,500 of a 2,000-page book that serialises 1,500 pages of results per page processed — O(n²) I/O. Flagged as F-3b, fixed under P1 because the real-document run is what will expose it.
- `count_pages()` correctly fast-paths through PyMuPDF before falling back to a full stream. ✅

### 2.6 Security posture

| Control | State |
|---|---|
| Secrets from env, masked in `Settings` | ✅ present |
| Upload magic-byte + size validation | ✅ `infrastructure/security.py` |
| Path-traversal guards on export paths | ✅ |
| Parameterised SQL in `corrections_store` | ✅ |
| `subprocess` invoked with argument lists, never shell strings | ✅ `ketos_trainer`, `calamari` |
| `bandit` + `pip-audit` in CI | ✅ ubuntu-only, acceptable |
| GPLv3 isolation enforced mechanically | ✅ `check_license_isolation.py` |
| **LICENSE file** | ❌ **absent** — README badges MIT. Blocks distribution. |

Security is the strongest non-architectural dimension of the codebase. The one gap is legal, not technical.

### 2.7 Technical debt register

| ID | Debt | Severity | Location |
|---|---|---|---|
| TD-1 | Kraken double segmentation; `IOCREngine` takes a page, not a line | High | `ports/interfaces.py:36`, `pipeline.py:333` |
| TD-2 | Checkpoint writes full document per page (O(n²)) | High at scale | `pipeline.py:247` |
| TD-3 | Four parallel copies of the app: `editions/{desktop,server,cloud}`, `editions/legacy-*`, root `omniocr/`, `prototype/` | Medium | repo root |
| TD-4 | Training orchestrator evaluation stubs raise `NotImplementedError` | High (blocks v2) | `training_orchestrator.py:224,236` |
| TD-5 | `PromotedModel` carries no checkpoint path — router resolves engine family, never the fine-tuned weights | High (blocks v2) | audit CQ1 |
| TD-6 | Lexicons are ~60 hardcoded words each | Medium | `infrastructure/lexicons.py:31,94` |
| TD-7 | `mypy --follow-imports=skip` weakens the strict-typing gate | Medium | `.github/workflows/ci.yml:30` |
| TD-8 | `.coverage` and `omniocr_jobs.db` are git-tracked despite `.gitignore` | Low | repo root |
| TD-9 | `pyproject.readme = "docs/BUILD_PLAN.md"` — the wheel ships the internal engineering plan as its package description | Low | `pyproject.toml:11` |
| TD-10 | 14 overlapping docs (4 audit/plan reports, 3 architecture docs) | Low | `docs/` |
| TD-11 | No metrics export; `BUILD_PLAN` §4.18 specifies `prometheus-client` | Medium | absent |
| TD-12 | `models.load_any` deprecated in Kraken 6, removed in 8 | Low, dated | `kraken.py:128` |

---

## 3. Scope Register

### 3.1 Fixes

| ID | Title | Priority | Phase |
|---|---|---|---|
| F-1 | Kraken recognition is broken (non-bi-level image → legacy segmenter) | **P0** | P0 |
| F-2 | Regression corpus is synthetic; baselines invalid | **P0** | P1 |
| F-3 | Pipeline never validated against a real document | **P0** | P1 |
| F-3b | Checkpoint write amplification (O(n²)) | High | P1 |
| F-4 | Training orchestrator evaluation stubs raise `NotImplementedError` | High | P3 |
| F-5 | `PromotedModel` loses the fine-tuned checkpoint path | High | P3 |
| F-6 | No entry point — no `[project.scripts]`, no CLI | High | P2 |
| F-7 | Duplicate/legacy edition trees | Medium | P2 |
| F-8 | Lexicons are stubs | Medium | P3 |
| F-9 | CI installs no OCR engines → accuracy tests always skip; mypy follow-imports=skip | High | P1 |
| F-10 | Release hygiene: no LICENSE/CHANGELOG, stale README badges, wrong `readme` target | Medium | P2 |
| F-11 | Tracked build artifacts (`.coverage`, `*.db`) | Low | P0 |

### 3.2 Enhancements

| ID | Title | Priority | Phase |
|---|---|---|---|
| E-1 | Line-level recognition port (`ILineRecognizer`) — kills double segmentation, feeds baseline polygons to the recognizer | High | P0/P1 |
| E-2 | Parallel page processing (`ThreadPoolExecutor`) | Medium | P1 |
| E-3 | Metrics export + run correlation IDs | Medium | P3 |
| E-4 | `omniocr` CLI (ingest → recognise → export, headless) | High | P2 |
| E-5 | Ground-truth capture in the review UI wired to `ICorrectionStore` | High | P3 |
| E-6 | Router resolves promoted fine-tuned checkpoints | High | P3 |

---

## 4. Impact Assessment

| Item | Domain | Ports | Application | Infrastructure | Composition | Editions | Tests | CI | Docs |
|---|---|---|---|---|---|---|---|---|---|
| F-1 | — | — | — | `kraken.py` | — | — | `test_engine_accuracy`, `test_contracts` | unblocks 2 gates | ARCH §4 |
| F-2 | — | — | — | — | — | — | `corpus/`, `test_regression`, `test_engine_accuracy` | new job | corpus README |
| F-3 | — | — | `pipeline.py` | `ingest.py`, `jobs.py` | — | desktop | new `test_large_document` | `-m slow` job | RUNBOOK |
| F-3b | — | `IJobStore` | `pipeline.py` | `jobs.py` | — | — | `test_core` resume tests | — | — |
| F-4 | — | `IEvaluator` | `training_orchestrator`, `evaluation` | — | `training.py` | — | `test_training_v2` | `-m slow` | V2 plan |
| F-5 | `training.py` | `IModelRegistry` | `router.py` | `mlflow_registry` | `desktop.py` | — | `test_router`, `test_promotion` | — | ADR |
| F-6 / E-4 | — | — | — | — | new `cli.py` | all | new `test_cli` | smoke job | README |
| F-7 | — | — | — | — | — | delete legacy | — | faster | README |
| E-1 | — | **`interfaces.py`** | `pipeline.py` | `kraken`, `tesseract`, `line_cropper` | `desktop.py` | — | `test_contracts` (new suite) | — | ADR-required |
| E-2 | — | — | `pipeline.py` | — | `desktop.py` | desktop UI progress | concurrency tests | — | — |
| E-5 | — | `ICorrectionStore` | — | `review.py` | `desktop.py` | `review_ui.py` | `test_review` | — | RUNBOOK |

**Highest blast radius: E-1.** It changes a core port, so every engine adapter and the orchestrator move together. It requires an ADR and a dedicated review. It is also the change that makes Kraken good rather than merely functional.

---

## 5. Phased Roadmap

```
P0 ──► P1 ──► P2
        └────► P3
```

P2 (packaging) and P3 (training loop) both depend on P1 and may run in parallel by different owners. Nothing depends on P3.

### Phase 0 — Restore the primary recognizer (3–5 days)

**Milestone P0-M1:** `pytest -m slow` green; Kraken produces Greek text on every fixture.
**Milestone P0-M2:** Kraken CER ≤ Tesseract CER on `polytonic-1` and `ancient-1`, recorded in `engine_baselines.json` under a `kraken` key.

Entry criteria: none. Exit criteria: zero failing tests, Kraken keys present in baselines, ADR-0xx recorded if E-1 is taken now rather than in P1.

### Phase 1 — Make measurement honest (5–8 days)

**Milestone P1-M1:** ≥ 3 real scanned pages per script variety (modern, polytonic, ancient, byzantine, pontian) with diplomatic ground truth, provenance, and licence recorded.
**Milestone P1-M2:** Baselines recomputed against real scans; CI installs Tesseract + `ell`/`grc` and Kraken so accuracy tests **run** rather than skip.
**Milestone P1-M3:** Full run over `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf` with recorded peak RSS, wall time, per-page CER sample, and a successful kill-and-resume.

Entry criteria: P0-M2. This is the phase that converts the project from "architected" to "evidenced".

### Phase 2 — Make it a product (4–6 days)

**Milestone P2-M1:** `pip install .` then `omniocr run book.pdf --out book.docx` works on a clean machine with no repository checkout.
**Milestone P2-M2:** LICENSE, CHANGELOG, accurate README; legacy trees deleted; `v0.2.0` tagged.

### Phase 3 — Close the v2 training loop and observability (6–10 days)

**Milestone P3-M1:** A fine-tuned Kraken model, trained from review-UI corrections, is evaluated on the held-out split and **refused or promoted** by the real policy — no stubs in the path.
**Milestone P3-M2:** The router loads the promoted checkpoint and the promoted model measurably lowers CER on its target typeface.
**Milestone P3-M3:** Metrics exported; every run carries a correlation ID through logs, events, and output provenance.

---

## 6. Work Breakdown Structure

---

### F-1 — Kraken recognition is broken

**Objective.** Make `KrakenEngine.extract` return real Greek text, using the segmentation path that is appropriate for historical print.

**Affected components.** `infrastructure/kraken.py` (both classes); `tests/test_engine_accuracy.py`; `tests/test_contracts.py`; `tests/corpus/engine_baselines.json`.

**Root cause.** `KrakenEngine.extract` (`kraken.py:129–130`) opens the grayscale PNG emitted by `GrayscaleProcessor` and calls `kraken.pageseg.segment`, the **legacy box segmenter**, which requires a bi-level image. `KrakenLayoutAnalyzer.segment` (`kraken.py:52–55`) binarizes first and therefore does not crash. Two call sites, one binarization, divergent behaviour.

**Design change — RESOLVED 2026-08-01, and not as this plan first proposed.**

The original proposal was to move both call sites to Kraken's baseline segmenter
`kraken.blla.segment`. A spike killed it: on the installed **Kraken 7.0.3**,
`blla.segment` attempted a **6.1 GB** allocation inside `compute_segmentation_map`
for an 800×180 page and died with `DefaultCPUAllocator: not enough memory`. That
disqualifies it as the default on a CPU-only target, whatever its quality
advantage on skewed historical lines.

Shipped instead: one module-level `_open_bilevel()` helper used by **both**
Kraken entry points, keeping `pageseg`. This is still the DRY fix that closes
the *class* of defect — two call sites binarizing independently and drifting —
not just the instance. Carries a `ponytail:` comment naming the ceiling (fixed
128 threshold) and the `blla` upgrade path.

(This plan also said Kraken 6.x. Installed version is 7.0.3.)

**Implementation tasks.**
1. **Spike (0.5 d, do this first).** Confirm the `blla.segment` and `rpred.rpred` signatures against the installed Kraken 6.x. The suite already emits `TorchVGSLModel.load_model is deprecated … use kraken.registry.load_model` (TD-12) — resolve the model-loading call in the same pass.
2. Add `_open_page_image(page: RawPage) -> Image` helper; single source of truth for decode + mode.
3. Repoint `KrakenEngine.extract` to `blla.segment` → `rpred.rpred(model, image, segmentation)`.
4. Repoint `KrakenLayoutAnalyzer.segment` to `blla.segment`; delete the `mode == "L"` binarization branch.
5. Update `_bounds` for baseline-polygon geometry (`blla` returns `baseline` + `boundary`; the current code already probes `boundary`/`polygon` — verify against real output, do not assume).
6. Replace `models.load_any` with the non-deprecated loader; keep a try/except fallback for Kraken < 6.
7. Run `scripts/compute_engine_baselines.py`, commit `kraken` keys.

**Refactoring requirements.** No port change in this fix. `pageseg`-specific handling is removed from the default path. If a fallback is kept it becomes a constructor-injected strategy, not an inline branch.

**Testing strategy.**
- Existing: `test_kraken_beats_tesseract_on_hard_scripts` (currently failing) becomes the primary gate.
- New: `test_kraken_accepts_grayscale_page` — asserts `extract()` returns `Ok` for exactly the `L`-mode PNG that `GrayscaleProcessor` produces. This is the regression test for the actual defect and must fail against `60aaafb`.
- New: `test_kraken_and_analyzer_agree_on_line_count` — the two call sites must not diverge again.
- Contract suite (`test_contracts.py`) already parametrises over Kraken; confirm it exercises the grayscale path.

**Acceptance criteria.**
1. Full suite green, including `-m slow`.
2. Kraken emits non-empty text on all four fixtures.
3. `kraken` keys committed in `engine_baselines.json`.
4. Kraken CER ≤ Tesseract CER on `polytonic-1` and `ancient-1` (`BUILD_PLAN` §10 Phase 1 acceptance).
5. No deprecation warnings from Kraken model loading.

**Rollback.** Single-file, feature-flag-free change on a branch. Revert the commit. Retain the new grayscale regression test on revert — it correctly documents the requirement even when unmet.

---

### F-2 — Replace the synthetic corpus with real scans

**Objective.** Make the CER regression gate measure OCR of printed Greek rather than a font round-trip.

**Affected components.** `tests/corpus/` (all fixtures, both baseline JSONs, README); `scripts/generate_fixtures.py`; `scripts/compute_engine_baselines.py`; `omniocr/testing/fixtures.py`; CI.

**Design change.** Two fixture *tiers*, explicitly separated so the distinction can never blur again:

- `tests/corpus/synthetic/` — the existing PIL renders. Retained, honestly labelled, used only for **harness** tests (does the metric compute, does the gate trip). Never used for accuracy claims.
- `tests/corpus/scans/` — real page images with diplomatic ground truth. The **only** source for accuracy baselines.

`omniocr/testing/fixtures.py` grows `list_scan_ids()` alongside `list_fixture_ids()`; accuracy tests switch to the former.

Each scan carries a sidecar `*.meta.json`: source, edition, year, typeface, script variety, licence, transcriber, transcription date.

**Implementation tasks.**
1. Source ≥ 3 pages per variety. Public-domain digitisations (Internet Archive, BSB, Anemi) cover modern/polytonic/ancient/byzantine. **Pontian is the acquisition risk** — start sourcing on day 1 of P1.
2. Transcribe diplomatically. This is human work and is the schedule driver; budget ~1 h/page for polytonic and Byzantine.
3. NFC-normalise every ground-truth file (Unicode hygiene only — `CLAUDE.md` rule 5).
4. Split the corpus directory; add `*.meta.json`; rewrite `tests/corpus/README.md` to state the tier rule.
5. Point accuracy tests at `scans/`; leave `test_regression.py` on `synthetic/`.
6. Recompute and commit baselines for Tesseract and Kraken.
7. Raise `MAX_PLAUSIBLE_CER` deliberately — real scans will not hit 0.0076. Set it from measured data, with a comment recording the measurement date and engine versions.

**Refactoring requirements.** `generate_fixtures.py` moves under a `synthetic` subcommand and its docstring states plainly that its output must never back an accuracy claim.

**Testing strategy.** Meta-tests: every scan has ground truth, meta, and a baseline entry; every ground-truth file is NFC-normalised; no accuracy test imports from `synthetic/`.

**Acceptance criteria.** ≥ 15 real pages across 5 varieties; baselines recomputed; `test_engine_baselines_cover_every_fixture` passes over `scans/`; polytonic baseline CER is a real measured number, not 0.0.

**Rollback.** Additive. Old fixtures survive under `synthetic/`; reverting the test-pointer commit restores the previous (weaker) gate.

---

### F-3 — Validate against the real target document

**Objective.** Prove the streaming, memory, and resume claims on the 339 MB PDF the project exists to read.

**Affected components.** `application/pipeline.py`; `infrastructure/ingest.py`; `infrastructure/jobs.py`; `docs/RUNBOOK.md`; new `tests/test_large_document.py`.

**Design change.** None expected up front — this is a *validation* task whose purpose is to surface design changes. F-3b below is the one defect already visible by inspection.

**Implementation tasks.**
1. Run the desktop pipeline over the full PDF with RSS sampling and per-page wall time.
2. Record results in `docs/RUNBOOK.md`: peak RSS, total time, pages/minute, failure count, failure causes.
3. Kill the process at ~40 % and resume from checkpoint; assert output equivalence with an uninterrupted run over the same page range.
4. Spot-check CER on 5 sampled pages against hand transcription.
5. Open defects for whatever this surfaces; do not fix opportunistically inside the validation task.

**Testing strategy.** A `@pytest.mark.slow` test over a **20-page excerpt** (committed, licence permitting) asserting bounded peak RSS and successful resume. The full 339 MB run stays a manual, documented procedure — CI must not carry it.

**Acceptance criteria.** Full document completes; peak RSS bounded and recorded; resume produces byte-identical pages; results written to the runbook.

**Rollback.** Read-only validation. Nothing to roll back.

---

### F-3b — Checkpoint write amplification

**Objective.** Remove O(n²) checkpoint I/O.

**Root cause.** `pipeline.py:247–252` serialises the entire accumulated `DocumentStructure` after every page.

**Design change.** Extend `IJobStore` with an append semantic — `append_page(job_id, page)` — and keep `checkpoint()` for a final/whole-document write. Adapters accumulate; `load()` reassembles. Alternatively checkpoint every N pages (configurable, default 25), which is the smaller diff. **Take the smaller diff unless the real-document run shows it is insufficient** — F-3 provides the measurement that decides this.

**Testing strategy.** Existing `test_pipeline_resume_skips_completed_pages` must stay green. Add a test asserting checkpoint call count is sub-linear in page count.

**Acceptance criteria.** 2,000-page checkpoint I/O within a documented budget; resume semantics unchanged.

**Rollback.** Port addition is backward-compatible if the new method is optional and the orchestrator feature-detects it. Revert the orchestrator commit alone.

---

### F-4 / F-5 / E-6 — Close the training loop

**Objective.** Make the v2 training pipeline run end to end: corrections → samples → train → evaluate → promote → route.

**Affected components.** `application/training_orchestrator.py:224,236`; `application/evaluation.py`; `domain/training.py`; `application/router.py`; `infrastructure/mlflow_registry.py`; `composition/training.py` and `composition/desktop.py`.

**Design change.**
- **F-4.** `_CandidateEngine` / `_ParentEngine` are placeholders that raise `NotImplementedError`, so no candidate can be evaluated. Replace with real adapters that instantiate `KrakenEngine` against the candidate's checkpoint path. This is unblocked by F-1 — before F-1 there was no working Kraken to instantiate.
- **F-5.** `PromotedModel` carries `model_ref` but not the checkpoint `path` that `ModelCandidate` had, so promotion loses the artefact. Add `checkpoint: Path` to `PromotedModel` and thread it through `IModelRegistry`.
- **E-6.** `RegistryAwareRouter` currently resolves `promoted.model_ref.engine` to an *engine family* from `engine_map`. With F-5 landed it must construct or look up an engine bound to the promoted checkpoint. Introduce a small `IEngineFactory` keyed by `ModelRef` rather than widening `engine_map` — widening a `Mapping[str, IOCREngine]` to carry per-checkpoint instances would smear model lifecycle across the router.

**Implementation tasks.** Spike the candidate-engine construction path; add `checkpoint` to `PromotedModel` with `__post_init__` existence validation; migrate the MLflow registry schema; add `IEngineFactory` to `ports/interfaces.py`; wire in `composition/desktop.py`; write the end-to-end training test.

**Testing strategy.**
- Unit: `PromotedModel` rejects a missing checkpoint path.
- Unit: router returns an engine bound to the promoted checkpoint, not the parent.
- Integration (`slow`): committed correction set → train (tiny epoch budget) → evaluate on held-out → policy decision. **Assert on the decision, not on an improvement** — a real fine-tune on a toy set may legitimately be refused, and a test that demands promotion would incentivise weakening the policy.
- Existing `test_promotion.py` five refusal rules must stay green untouched.

**Acceptance criteria.** No `NotImplementedError` on the training path; `PromotedModel` carries a verified checkpoint; the router loads it; the end-to-end test runs the real policy; all v1 faithfulness tests still green.

**Rollback.** `[training]` is an optional extra and `composition/training.py` is a separate root. Reverting the training commits cannot affect the recognition pipeline — a property worth preserving deliberately.

---

### F-6 / E-4 — Entry point and CLI

**Objective.** Make OmniOCR installable and runnable without a repository checkout.

**Affected components.** `pyproject.toml`; new `packages/omniocr/src/omniocr/interfaces/cli.py`; README.

**Design change.** Add `[project.scripts] omniocr = "omniocr.interfaces.cli:main"`. The CLI is a **composition root**, not logic: parse args → call `create_desktop_pipeline()` → run → export. `argparse` from stdlib; no Click, no Typer — one command with four flags does not justify a dependency.

```
omniocr run BOOK.pdf --out book.docx [--script polytonic] [--engine kraken|tesseract|ensemble] [--resume JOB_ID]
omniocr doctor      # Tesseract present? which language packs? models present and hash-valid?
```

`doctor` is not scope creep — it is the support-cost fix for a product whose main failure mode is a missing `grc` language pack, which is exactly the failure that today surfaces as a silent test skip.

**Implementation tasks.** Create `interfaces/` layer; implement `run` and `doctor`; register the script; document in README; add a CI smoke job that pip-installs the wheel into a clean venv and runs `omniocr doctor`.

**Testing strategy.** Unit-test argument parsing and exit codes. CI smoke: install wheel → `omniocr doctor` → `omniocr run` over a 2-page fixture → assert the output file exists and is non-empty. This closes the "works on the maintainer's machine" gap.

**Acceptance criteria.** Clean-machine install runs a PDF to DOCX. `doctor` correctly reports a missing `grc` pack. Non-zero exit on failure.

**Rollback.** Purely additive.

---

### F-7 — Consolidate edition trees

**Objective.** One implementation per edition.

**Design change.** Delete `editions/legacy-{desktop,server,cloud}`, root `omniocr/`, and — once the CLI (E-4) reaches parity — `prototype/`. `CLAUDE.md` already designates `ocr.py` as retired-to-prototype at Phase 1 parity; that condition is met by E-4.

**Implementation tasks.** Confirm zero inbound imports (`grep`, not assumption); confirm no test references; delete in one commit per tree so each is independently revertible; update README's edition table.

**Acceptance criteria.** Suite green; no import references remain; README matches the tree.

**Rollback.** `git revert` per tree. Deleted code stays in history — that is what history is for.

---

### F-8 — Real lexicons

**Objective.** Replace ~60-word placeholder lists with usable vocabularies.

**Design change.** Lexicons become **bundled data files**, not Python tuples. `SetLexicon` already loads from `{name}.txt` (`lexicons.py:22–28`) — the loader exists; the data does not. Source modern Greek from Hunspell `el_GR`, ancient from CLTK. Pontian has no standard machine-readable lexicon; expect a curated list and treat coverage as partial and documented.

**Critical constraint.** These are **suggest-only** (`CLAUDE.md` rule 1). Enlarging them must not enlarge their authority. The property test "corrector output leaves source text byte-identical" is the guard and must remain green across the change.

**Acceptance criteria.** Modern lexicon ≥ 50k entries; ancient ≥ 20k; Pontian curated with documented coverage; faithfulness property tests unchanged and green; suggestion volume on a fixture page recorded so the review UI is not drowned.

**Rollback.** Data-only. Revert the data commit.

---

### F-9 — CI gates that actually gate

**Objective.** Stop accuracy tests silently skipping, and make strict typing strict.

**Design change.**
- Add a CI job that installs Tesseract with `ell` + `grc` and Kraken, then runs the accuracy suite **without** the skip escape. Keep the existing fast matrix engine-free so the base-install-stays-green property (`test_engine_accuracy.py:11–13`) is still verified.
- Add a `-m slow` job (Kraken inference, training E2E) on a schedule or on `main`, not on every push.
- Drop `--follow-imports=skip` from the mypy invocation. Expect a burst of genuine cross-module findings; fix them rather than re-adding the flag.
- Fail CI when the accuracy job **skips** rather than runs — a skipped gate that reports green is worse than no gate.

**Acceptance criteria.** Accuracy job runs and passes on Linux; mypy strict passes without `--follow-imports=skip`; skipped accuracy tests fail the job.

**Rollback.** Workflow-only; revert the YAML.

---

### F-10 / F-11 — Release and repo hygiene

**Tasks.**
1. Add `LICENSE` (MIT, matching the README badge). **Blocking for any distribution.**
2. `pyproject.readme` → `README.md` (currently ships `docs/BUILD_PLAN.md` as the package description).
3. Add `CHANGELOG.md` (Keep a Changelog); tag `v0.2.0` at the end of P2.
4. Regenerate README badges from measured values; add a CI step that fails when the badge count diverges from the collected test count.
5. `git rm --cached .coverage omniocr_jobs.db omniocr_cloud_jobs.db` and any tracked `*.pyc` — `.gitignore` already lists them but cannot act on already-tracked files.
6. Add `CONTRIBUTING.md` and `SECURITY.md`.
7. Consolidate `docs/`: one architecture document, one plan of record, ADRs; archive the rest under `docs/archive/`.

**Acceptance criteria.** LICENSE present; `python -m build` produces a wheel whose description is the README; no tracked build artifacts; badges match reality.

---

### E-1 — Line-level recognition port

**Objective.** Let the layout analyser's segmentation reach the recogniser: one segmentation pass, baseline polygons preserved.

**Design change.** Add a port alongside — not replacing — the existing one:

```python
class ILineRecognizer(Protocol):
    name: str
    def recognize(
        self, line_image: bytes, line: OCRLine, context: TenantContext
    ) -> Result[OCRBlock, EngineError]: ...
```

`PipelineOrchestrator._process_page` prefers `ILineRecognizer` when the routed engine provides it and falls back to whole-page `IOCREngine.extract` otherwise. Tesseract keeps whole-page extraction (it is the source of word boxes for searchable PDF and is good at its own layout); Kraken moves to line-level.

Line cropping reuses the existing `ILineCropper` / `PilLineCropper` — already built for the training path. One cropper, two consumers: recognition and ground-truth assembly. That is DRY earned by a second real caller, not speculative generality.

**Justification against YAGNI.** Two independent payoffs, both measured rather than assumed: (a) removes a duplicated segmentation pass on the most expensive stage of a CPU-only product; (b) preserves baseline polygons, which is the mechanism by which Kraken outperforms Tesseract on skewed historical print. **Gate this work on P0 measurement** — if F-1 alone already clears the Kraken-beats-Tesseract bar with margin, E-1 is deferred to P1 and justified on the performance number alone.

**Refactoring requirements.** ADR required (core port change). Contract test suite extended to cover both ports. Engines declaring both must return equivalent text for the same line — a new contract test.

**Testing strategy.** Contract suite over every adapter; equivalence test between whole-page and line-level output; benchmark asserting a measured reduction in segmentation calls per page.

**Acceptance criteria.** Kraken runs line-level; segmentation call count per page drops from 2 to 1; CER does not regress; all engines still satisfy their contract suites.

**Rollback.** Additive port with orchestrator feature-detection — reverting the composition wiring restores whole-page behaviour without touching the port.

---

### E-2 — Parallel page processing

**Objective.** Use available cores on an embarrassingly parallel workload.

**Design change.** `ThreadPoolExecutor` over pages inside `run()`, bounded queue for back-pressure, worker count from config (default `cpu_count - 1`). Engine adapters must be confirmed thread-safe or pooled per worker — **verify, do not assume**; Kraken model objects in particular are stateful.

**Gate.** Do not build this before F-3 produces a real wall-clock number. If a 400-page book already completes in an acceptable time, this is optimisation without a problem.

**Testing strategy.** Determinism test: parallel output must equal serial output page-for-page. Failure-isolation test: one bad page must not kill the job. Resume-under-concurrency test.

**Acceptance criteria.** Measured throughput improvement on the real document; identical output to serial; page ordering preserved in the exported structure.

**Rollback.** Config flag `workers=1` restores serial behaviour without a code revert — the correct rollback design for a concurrency change.

---

### E-3 — Observability completion

**Objective.** Satisfy `BUILD_PLAN` §1.4 and §4.18: structured logs, per-page metrics, end-to-end provenance.

**Design change.** `prometheus-client` in the `[server]`/`[cloud]` extras with a no-op sink for desktop, fed by the existing `IEventBus` — the Observer seam is already there, so this is wiring, not new architecture. Add a `run_id` (UUID) generated at pipeline start and carried through every log line, every `PipelineEvent`, and into export metadata, so an exported page can be traced back to the run that produced it.

Metrics: pages processed, page duration histogram, per-engine invocation count, mean confidence, review-flag count, failure count by error type.

**Acceptance criteria.** `/metrics` served by server/cloud editions; desktop unaffected and dependency-free; `run_id` present in logs, events, and export metadata; a page's provenance resolves to engine + model hash + run.

**Rollback.** Additive; no-op sink is the default.

---

### E-5 — Ground-truth capture in the review UI

**Objective.** Close the loop that makes the training pipeline (P3) have anything to train on.

**Design change.** `review_line_to_correction()` already bridges `ReviewLine` → `Correction` (`infrastructure/review.py`), and `SqliteCorrectionStore` is append-only. The missing link is the UI calling it. Wire the correction store into `create_desktop_pipeline()` and have `editions/desktop/review_ui.py` append on accept.

**Constraint.** The store is append-only by design. The UI must never update or delete a correction — an undo is a *new* correction, not a mutation. This is the same faithfulness discipline as the suggestion layer and must be tested, not merely intended.

**Refactoring requirement.** `review_ui.py` is 590 lines with function-local imports and inline domain construction. Extract a view-model module before adding to it (`BUILD_PLAN` §4.15 specifies MVVM). Adding features to it as-is compounds debt on the one surface a scholar actually touches.

**Acceptance criteria.** Accepting a correction persists it; corrections survive restart; the training orchestrator reads them; an undo appends rather than mutates; the append-only property is covered by a test.

**Rollback.** Store injection is optional — pass `None` to disable. Revert the UI commit.

---

## 7. Risk & Mitigation Matrix

| # | Risk | Likelihood | Impact | Mitigation | Owner phase |
|---|---|---|---|---|---|
| R-1 | ~~`blla.segment` does not fix F-1~~ **MATERIALISED 2026-08-01.** `blla` OOM'd at 6.1 GB; shipped `_open_bilevel` + `pageseg` instead. Crash fixed, Kraken recognizes — but measured **CER 0.70 (polytonic) / 0.71 (ancient)** vs Tesseract 0.00 / 0.01 on the *synthetic* corpus. Cannot tell yet whether Kraken is weak or the fixtures are simply out of its domain. | — | **Critical** — the accuracy thesis is still unproven | Resolved by F-2, not by more engine work. The committed Ciaconna models target 19c Porson/German-serif print; the fixtures are Arial renders, maximally out of domain. Accuracy assertion is `xfail`-marked with the measured numbers, **not** relaxed. Do not tune to the synthetic fixture. | P1 |
| R-2 | Pontian scans unobtainable or unlicensable | Medium | High — a named scope variety loses its gate | Start sourcing on day 1 of P1. Fallback: document the gap explicitly in README and ARCHITECTURE rather than shipping a synthetic stand-in that implies coverage. | P1 |
| R-3 | Diplomatic transcription is slower than budgeted | High | Medium — P1 slips | Start with 1 page per variety to unblock the harness, then deepen. Prefer sources with existing scholarly transcriptions. | P1 |
| R-4 | Real-scan baselines are so much worse than synthetic that the product looks broken | High | Medium (perception) | Expected and correct. Publish both numbers with the tier explanation. A real 8 % CER is more valuable than a fake 0.76 %. | P1 |
| R-5 | Dropping `--follow-imports=skip` surfaces a large backlog of type errors | High | Medium — P1 slips | Timebox. If the backlog exceeds the box, land per-module strictness in `[[tool.mypy.overrides]]` and burn down on a schedule. Do not re-add the global flag. | P1 |
| R-6 | E-1 port change destabilises working Tesseract output | Medium | High | Additive port + feature detection; Tesseract stays whole-page; contract equivalence test; ADR + dedicated review. | P0/P1 |
| R-7 | Kraken model objects are not thread-safe (E-2) | Medium | Medium | Verify explicitly; pool one model instance per worker; determinism test as the gate. | P1 |
| R-8 | 339 MB run exhausts memory or takes impractically long | Medium | High | This is the point of F-3 — discovery, not failure. F-3b and E-2 are the pre-identified responses. | P1 |
| R-9 | Larger lexicons flood the review UI with suggestions | Medium | Medium | Measure suggestions-per-page before and after; add a confidence/priority threshold if volume degrades review throughput. | P3 |
| R-10 | Training-loop work leaks dependencies into the inference install | Low | High | Already mitigated structurally: separate composition root + `[training]` extra + `check_license_isolation.py`. Add a test asserting `composition/desktop.py` imports no training module. | P3 |
| R-11 | Deleting legacy trees removes something still referenced | Low | Medium | Grep for inbound imports before deletion; one commit per tree; full suite between. | P2 |
| R-12 | No LICENSE blocks distribution at the last moment | Certain if unaddressed | High | Trivial fix, scheduled early in P2 rather than at release. | P2 |

---

## 8. Testing & Quality Assurance Strategy

### 8.1 Pyramid

| Layer | Scope | Runs | Gate |
|---|---|---|---|
| Unit (pure) | domain invariants, correctors, reconcilers, router rules, promotion policy | every push | must pass |
| Property (`hypothesis`) | NFC idempotence, **correction never mutates source**, box arithmetic | every push | must pass |
| Contract | every adapter against its port suite; `IOCREngine` **and** `ILineRecognizer` after E-1 | every push | must pass |
| Integration | each engine over real scan fixtures | engine-installed job | must pass |
| **Accuracy regression** | CER/WER vs committed per-engine baselines | engine-installed job | **must pass, must not skip** |
| Export snapshot | DOCX/PDF/ALTO golden files, Byzantine font coverage, searchable-PDF overlays image | every push | must pass |
| E2E | CLI: install wheel → `doctor` → `run` → export | every push | must pass |
| Slow | Kraken inference, training loop, 20-page excerpt memory bound | `main` / scheduled | must pass |
| Manual | full 339 MB document + kill/resume | per release | recorded in runbook |

### 8.2 Non-negotiable invariants

These are the product, not the test suite. Any change that requires weakening one is rejected by default:

1. **Faithfulness.** Post-correction emits `Suggestion`s; source text bytes are unchanged. Property-tested.
2. **Grounding.** VLM text with no box overlap against a verifiable engine is never sole source.
3. **Provenance.** Every exported line resolves to engine + model hash + run.
4. **Append-only corrections.** No update, no delete.
5. **Licence isolation.** No `calamari_ocr` import reachable from core. Mechanically enforced.
6. **Base install green.** A no-extras install runs a green suite (engine tests skip *by design* there — and only there).

### 8.3 Coverage

≥ 80 % overall (existing CI gate), ≥ 95 % on `domain/` and post-correction. Coverage is necessary and not sufficient: the current 87 % coexisted with a recogniser that had never emitted a character. **Every P0/P1 fix ships with a test that fails against `60aaafb`.** That is the real gate.

### 8.4 Review standards

- Core port changes (E-1, F-5) require an ADR in `docs/adr/` and a second reviewer.
- Any PR touching `application/post_correction.py`, `application/reconcile.py`, or the grounding guard is a faithfulness review — the invariant list above is the checklist.
- No PR merges with a skipped accuracy test that should have run.

---

## 9. Deployment & Rollback Plan

### 9.1 Artifacts

| Edition | Artifact | Deploy |
|---|---|---|
| Desktop | wheel + `omniocr` console script | `pip install omniocr[pdf,kraken,tesseract,docx]` |
| Server | wheel + `editions/server` | uvicorn + RQ worker + Redis |
| Cloud | Docker image | `editions/cloud/docker-compose.yml` |

### 9.2 Sequence

1. Merge to `main` behind a green CI including the engine-installed accuracy job.
2. Tag `vX.Y.Z`; CI builds wheel + sdist.
3. Smoke: clean venv → install wheel → `omniocr doctor` → `omniocr run` over a 2-page fixture.
4. Publish; update CHANGELOG.
5. Cloud/server: rolling restart of workers. Jobs are checkpointed, so in-flight work resumes.

### 9.3 Rollback

| Layer | Mechanism | Cost |
|---|---|---|
| Code | `git revert` the phase branch merge | minutes |
| Package | reinstall previous version from index | minutes |
| Models | `models/manifest.json` is hash-pinned; restore the previous manifest entry | minutes |
| Baselines | baselines are committed data; revert the baseline commit | minutes |
| Concurrency (E-2) | set `workers=1` — config, no deploy | seconds |
| Training (P3) | separate composition root; reverting cannot affect recognition | minutes |
| Corrections DB | append-only, so no forward migration destroys history; a schema change ships with a documented backward-compatible read path | — |

**Design rule adopted here:** every risky change gets a rollback that is *config or revert*, never *restore from backup*. E-2's `workers=1` and E-1's feature-detection fallback are examples of that rule applied deliberately.

---

## 10. Post-Implementation Validation Checklist

### Phase 0 — Kraken restored
- [ ] `pytest` full suite green, zero failures
- [ ] `pytest -m slow` green
- [ ] Kraken emits non-empty Greek on all fixtures
- [ ] `test_kraken_accepts_grayscale_page` fails against `60aaafb`, passes now
- [ ] Kraken CER ≤ Tesseract CER on `polytonic-1` and `ancient-1`
- [ ] `kraken` keys committed in `engine_baselines.json`
- [ ] No Kraken deprecation warnings
- [ ] `.coverage` / `*.db` untracked

### Phase 1 — Honest measurement
- [ ] ≥ 15 real scanned pages, ≥ 3 per variety (or the Pontian gap explicitly documented)
- [ ] Every scan has ground truth + `*.meta.json` with licence and provenance
- [ ] All ground truth NFC-normalised
- [ ] `synthetic/` and `scans/` separated; accuracy tests read only `scans/`
- [ ] Baselines recomputed from real scans; no 0.0000 CER
- [ ] CI installs engines; accuracy job runs and **cannot silently skip**
- [ ] mypy strict passes without `--follow-imports=skip`
- [ ] Full 339 MB run complete; peak RSS, wall time, pages/min in `docs/RUNBOOK.md`
- [ ] Kill-and-resume produces byte-identical pages
- [ ] Checkpoint I/O sub-linear in page count

### Phase 2 — Product
- [ ] Clean-machine `pip install` → `omniocr run book.pdf --out book.docx` succeeds
- [ ] `omniocr doctor` correctly detects a missing `grc` pack
- [ ] LICENSE present and matching the README badge
- [ ] `pyproject.readme` → `README.md`; wheel description correct
- [ ] CHANGELOG present; `v0.2.0` tagged
- [ ] README badges regenerated from measured values
- [ ] `editions/legacy-*`, root `omniocr/`, `prototype/` deleted; suite green
- [ ] `docs/` consolidated; superseded plans archived

### Phase 3 — Training loop and observability
- [ ] No `NotImplementedError` reachable on the training path
- [ ] `PromotedModel` carries a validated checkpoint path
- [ ] Router loads the promoted checkpoint, not the parent
- [ ] End-to-end training test exercises the real promotion policy
- [ ] Corrections persist from the review UI and survive restart; undo appends
- [ ] `review_ui.py` split into view-model + view
- [ ] Lexicons at target size; faithfulness property tests still green
- [ ] `/metrics` served by server/cloud; desktop dependency-free
- [ ] `run_id` traceable through logs → events → export provenance

### Cross-cutting — all phases
- [ ] All six invariants in §8.2 hold
- [ ] Coverage ≥ 80 % overall, ≥ 95 % domain/post-correction
- [ ] `bandit`, `pip-audit`, licence isolation green
- [ ] Every P0/P1 fix has a test that fails against `60aaafb`
- [ ] ADRs recorded for every core port change

---

## 11. Engineering Practices Applied

| Practice | Where it binds in this plan |
|---|---|
| **SOLID — SRP** | `review_ui.py` split before extension (E-5); CLI is composition only, never logic (E-4) |
| **SOLID — OCP** | New capability arrives as new ports/strategies (`ILineRecognizer`, `IEngineFactory`), not by editing existing adapters |
| **SOLID — LSP** | Contract suites per port; adapters must be substitutable — enforced by test, not review |
| **SOLID — ISP** | New ports stay single-method; `IEngineFactory` is separate from `IModelRegistry` rather than widening it |
| **SOLID — DIP** | Concrete infrastructure named only in `composition/` — preserved by every change here |
| **Clean Architecture** | Dependency direction unchanged; new `interfaces/cli.py` sits in the outermost ring |
| **Separation of Concerns** | Fixture tiers split by *purpose* (harness vs accuracy), not convenience |
| **DRY** | One `_open_page_image` helper closes the F-1 defect class; one `ILineCropper` serves recognition and training |
| **KISS** | `argparse` over Click; checkpoint-every-N before an append-semantics port |
| **YAGNI** | E-1 and E-2 gated on measurement; manuscript HTR, GPU edition explicitly out of scope |
| **Secure by Design** | LICENSE before distribution; subprocess isolation retained; no new network surface |
| **Defensive Programming** | `PromotedModel` validates its checkpoint exists; `doctor` fails loudly on a missing language pack instead of skipping a test |
| **Observability** | E-3 rides the existing `IEventBus` Observer seam; `run_id` threads log → event → provenance |
| **CI/CD** | Cheap checks first; engine-installed accuracy job; slow job off the push path; **a skipped gate fails** |
| **Code review** | ADR + second reviewer for port changes; faithfulness checklist for correction-path PRs |
| **Documentation** | ADR per architectural decision; RUNBOOK carries measured numbers; README badges verified by CI |
| **Performance** | Every optimisation gated on a measurement from F-3; no speculative tuning |
| **Scalability** | Parallelism across pages, bounded queues, sub-linear checkpointing, warm model pool per worker |

---

## Appendix A — Primary files touched

```
packages/omniocr/src/omniocr/
├── ports/interfaces.py              E-1 (ILineRecognizer), F-5 (IEngineFactory)
├── domain/training.py               F-5 (PromotedModel.checkpoint)
├── application/pipeline.py          E-1, E-2, F-3b
├── application/router.py            E-6
├── application/training_orchestrator.py  F-4
├── infrastructure/kraken.py         F-1  ← start here
├── infrastructure/jobs.py           F-3b
├── infrastructure/lexicons.py       F-8
├── infrastructure/review.py         E-5
├── composition/desktop.py           E-1, E-5, E-6
└── interfaces/cli.py                E-4  (new)

editions/desktop/review_ui.py        E-5 (+ view-model extraction)
tests/corpus/{synthetic,scans}/      F-2
tests/test_engine_accuracy.py        F-1, F-2
.github/workflows/ci.yml             F-9
pyproject.toml                       F-6, F-10
LICENSE, CHANGELOG.md                F-10  (new)
```

## Appendix B — Reproducing the baseline findings

```bash
python -m pytest -q
```

```bash
python -m pytest tests/test_engine_accuracy.py -v -m slow
```

```bash
python scripts/compute_engine_baselines.py
```
