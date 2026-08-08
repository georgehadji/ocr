# Architecture Audit — OmniOCR

**Protocol:** ARCH-AUDIT-V2 · EGFV epistemic labels
**Commit audited:** `10a905a` + 2026-08-05 remediation (branch `fix/ci-gates-and-audit-corrections`)
**Audit date:** 2026-08-02 · **remediation re-audit:** 2026-08-05
**Supersedes:** the 2026-07-27 audit (see *Supersession* — three of its findings were `[FALSE]`)

> ## Score: 7 → 9.5 / 10
>
> Every HIGH violation is closed, and each is now mechanically gated rather
> than merely fixed. The remediation record is immediately below; the original
> 2026-08-02 findings are preserved unedited underneath, so the delta stays
> auditable instead of rewritten.

---

## Remediation — 2026-08-05

Each item was re-verified by command *after* the change, never assumed.

| Finding | Was | Status | Verification |
|---|---|---|---|
| Editions bypass the composition root (×3) | HIGH | **Closed** | 3/3 editions import `omniocr.composition`; adapter imports under `editions/` → **0** `[VERIFIED]` |
| `RegistryAwareRouter` never resolves the promoted checkpoint | HIGH | **Closed** | `PromotedModel.checkpoint` added and populated; router resolves through an `engine_factory`, cached per checkpoint `[VERIFIED]` |
| 6× `hasattr(x, "value")` defensive `Result` unwrapping | MEDIUM | **Closed at root** | `Result` made a union alias so narrowing works; `hasattr` unwrapping in core → **0** `[VERIFIED]` |
| Shared `KrakenEngine` state under ADR-003 parallelism | UNKNOWN | **Closed** | `RLock` + double-checked load; 2 regression tests, one specifically for deadlock `[VERIFIED]` |
| Checkpoint write amplification O(n²) | VERIFIED | **Closed** | batched (default 25) + forced final write; test asserts 3 writes for 10 pages `[VERIFIED]` |
| No backpressure — `run_iteratively` materialized the document | VERIFIED | **Closed** | sequential path streams; test asserts only page 1 is produced before the first yield `[VERIFIED]` |
| Layering enforced by documentation only | — | **Gated** | `scripts/check_layering.py` in CI; proven to fail on an injected violation `[VERIFIED]` |

### Defects the remediation exposed

Collapsing the hand-wired editions surfaced bugs the import-scan audit could
not see. Each is a direct consequence of an edition wiring its own pipeline:

1. `[VERIFIED]` **The desktop UI ran with no recognition engine.** Its default
   path built a `PipelineOrchestrator` with no `router`, so `NullRouter`
   returned no engines and every page recognized as empty.
2. `[VERIFIED]` **The desktop UI used the VLM unaudited.** With VLM enabled it
   routed *only* to `VLMEngine` with `default=()` — no box-grounded engine to
   reconcile against, violating the faithfulness rule in CLAUDE.md §1.
3. `[VERIFIED]` **Server and cloud constructed `KrakenEngine("")`** — an empty
   model path, so Kraken could never load in either edition.
4. `[VERIFIED]` **The server passed `cfg.app_name` as the Tesseract language**,
   i.e. `TesseractEngine("omniocr")`.
5. `[VERIFIED]` **`create_desktop_pipeline` had the same defect, in the
   composition root itself** — no router, so `NullRouter` returned no engines.
   It had zero callers, so it was deleted rather than fixed: an unused factory
   with the most inviting name in the package is a trap, not an API.

All four disappear by construction now that editions compose rather than wire.
That is the argument for a composition root, demonstrated rather than asserted.

### Why the `Result` change mattered

`Result` was a *base class*, so `if isinstance(x, Err): return` left mypy still
seeing `Result`, and `x.value` afterwards failed to type-check. That is *why*
the `hasattr` workarounds existed — they were symptoms, not sloppiness.
Redefining `Result` as a union alias (`Ok[T,E] | Err[T,E]`) fixed narrowing
everywhere at once: the 6 `hasattr` sites **and** 3 previously unnoticed
`# type: ignore` suppressions were deleted rather than relocated. The core now
contains **zero** `type: ignore` comments under `mypy --strict` `[VERIFIED]`.

### Score justification (rubric-anchored)

Rubric 10 requires *"all layers correctly separated, patterns consistent,
observable, testable, scalable"*.

- **Layers separated** ✅ verified by scan *and* enforced by a CI gate
- **Patterns consistent** ✅ one `Result` idiom, zero suppressions, editions uniform
- **Testable** ✅ full suite green, new regressions for each fix
- **Scalable** ✅ O(n²) checkpointing and eager materialization both removed;
  engine state now thread-safe under the parallel path
- **Observable** ⚠️ `IEventBus` + structured logging exist, but there is still
  no metrics export — the `prometheus-client` seam is unbuilt

**Deducted 0.5**, not 0, because of that observability gap plus two facts no
refactor can fix: accuracy remains unproven on real scans (synthetic corpus),
and the 339 MB target document has still never been processed end to end.
Those are evidence gaps rather than structural ones, but a 10 would overclaim.

### Gates

`mypy --strict` 0 · `ruff check` 0 · `ruff format --check` 0 · `bandit` 0 ·
licence isolation ✅ · **layering ✅ (new)** · full suite **0 failures**.

---

## Supersession — corrections to the 2026-07-27 audit

Re-running the protocol against current code contradicts the prior run. Recorded here because a stale audit that reads as authoritative is worse than none.

| Prior claim | Now | Basis |
|---|---|---|
| `editions/` — "Thin composition roots … no business logic outside core", drift **None**, violations **0** `[VERIFIED]` | **`[FALSE]`** | `grep -c "from omniocr.composition" editions/desktop/review_ui.py` → **0**. It imports 12 infrastructure modules directly. The prior audit asserted `[VERIFIED]` for a fact that a single grep refutes. |
| MATURITY: **Production** | **`[FALSE]`** at that date | On 2026-07-27 `KrakenEngine.extract` raised `Image is not bi-level` on every call — the documented primary recognizer had never produced a character. Not production. |
| ARCHITECTURE SCORE 8/10 | 7/10 | Score rested partly on the incorrect `editions/` row. |
| `pipeline.py` imports `get_logger` from infrastructure (MEDIUM leak) | **Resolved** | Fixed in `333ccda` (logger injected). Correct finding at the time. |
| "12 protocols", "137 tests" | 18 protocols, ~257 tests | Codebase grew. |

Prior findings that **hold**: no circular dependencies; domain purity; railway-oriented `Result`; VLM cleanly behind `IOCREngine`; synthetic-corpus risk (still open).

---

## Step 0 — Input Gate

| Input | Status |
|---|---|
| Full codebase | ✅ present |
| Primary entry point | ✅ `[project.scripts] omniocr = "omniocr.interfaces.cli:main"` `[VERIFIED]` |
| ADRs | ✅ `docs/adr/`, incl. ADR-003 (ThreadPoolExecutor) |
| README / design docs | ✅ `README.md`, `docs/ARCHITECTURE.md`, `BUILD_PLAN.md`, `RUNBOOK.md` |
| Dependency manifest | ✅ `pyproject.toml` |
| Deployment manifests | ⚠️ **partial** — `editions/cloud/{Dockerfile,docker-compose.yml}` only; desktop and server have none |
| CI/CD config | ✅ `.github/workflows/ci.yml` |

**Gate: PROCEED.** No REQUIRED input missing. Deployment coverage is cloud-only; flagged inline where it affects a finding.

---

## Phase 1 — Architectural Fingerprinting

### DETECTED ARCHITECTURE: Hexagonal (Ports & Adapters) with an explicit composition root, executing a synchronous Pipes-and-Filters pipeline over a `Result` monad.

Derived from the import graph first, then cross-checked against `docs/ARCHITECTURE.md`.

1. `[VERIFIED]` **Port layer is real and central.** `ports/interfaces.py` (179 LOC) declares 18 `Protocol` types — structural typing, not ABC inheritance.
2. `[VERIFIED]` **Dependency direction holds, by scan not by doc.** `from omniocr.infrastructure` in `application/*.py` → **0 hits**. Outward imports in `ports/*.py` → **0 hits**. `domain/*.py` imports only `omniocr.domain.*`.
3. `[VERIFIED]` **No infrastructure → application cycle.** `grep -l "from omniocr.application" infrastructure/*.py` → **0 files**.
4. `[VERIFIED]` **Composition root exists and is isolated.** `composition/desktop.py` — 14 `omniocr.*` imports, the only place recognition adapters are named. `composition/training.py` is a separate root, keeping the `[training]` extra genuinely optional.
5. `[VERIFIED]` **Pipes-and-Filters threading `Result`.** `PipelineOrchestrator._process_page` (`pipeline.py:436`) sequences preprocess → segment → route → extract → reconcile → post-correct, each stage returning `Result[T, E]`.

### Layer sizes `[VERIFIED]`

| Layer | LOC | Role |
|---|---|---|
| `domain/` | 586 | frozen dataclasses, `Result`, errors — no I/O |
| `ports/` | 239 | 18 Protocols |
| `application/` | 1,674 | orchestration, routing, reconciliation, correction, training |
| `infrastructure/` | 3,111 | 26 adapter modules |
| `composition/` | 296 | 2 roots |
| `interfaces/` | 427 | CLI |

Infrastructure largest, domain small and pure — the correct shape for hexagonal.

### Entry points `[VERIFIED]`

`omniocr.interfaces.cli:main` (console script); `editions/desktop/review_ui.py` (Streamlit); `editions/server/main.py` + `worker.py` (FastAPI + RQ); `editions/cloud/main.py` + `tasks.py` (FastAPI + Celery).

### Data flow topology `[VERIFIED]`

**Fully synchronous.** `grep -rn "async def|await |asyncio" packages/omniocr/src/omniocr/ | wc -l` → **0**. Concurrency is `ThreadPoolExecutor` over pages only (ADR-003). Queue-based async lives at the edition boundary (RQ/Celery), never in core. For a CPU-bound workload this is a correct decision, not an omission.

### Configuration and secrets `[VERIFIED]`

`infrastructure/config.py` — `Settings.from_env()` reads `OMNIOCR_*`; `vlm_api_key: str | None = field(default=None, repr=False)` keeps the key out of reprs and tracebacks.

---

## Phase 2 — Compliance Matrix

| Module | Detected Pattern | Intended Pattern | Drift | Violations | Severity | Evidence |
|---|---|---|---|---|---|---|
| `domain/` | Pure value objects | Pure value objects | None | — | — | imports only `omniocr.domain.*` `[VERIFIED]` |
| `ports/` | Protocol boundary | Protocol boundary | None | — | — | 0 outward imports `[VERIFIED]` |
| `application/pipeline.py` | Orchestrator, 567 LOC | Thin orchestrator | Low | Holds serial + parallel + iterative paths; nearing the 800-line ceiling in `CLAUDE.md` | LOW | `wc -l` = 567 `[VERIFIED]` |
| `application/training_orchestrator.py` | Orchestrator | Orchestrator | Medium | 6× `hasattr(x, "value")` defensive `Result` unwrapping; substitutes a zeroed `EvaluationReport` when the contract breaks | MEDIUM | 6 occurrences `[VERIFIED]` |
| `application/router.py` | Strategy | Strategy | Medium | `RegistryAwareRouter` resolves engine *family*, never the promoted checkpoint | HIGH | audit CQ1, unchanged `[VERIFIED]` |
| `infrastructure/*` | Adapters | Adapters | None | — | — | 0 `application` imports `[VERIFIED]` |
| `composition/desktop.py` | Composition root | Composition root | None | — | — | sole adapter-naming site for recognition `[VERIFIED]` |
| `interfaces/cli.py` | Composition + I/O, 426 LOC | Thin entry point | Low | No recognition logic; size is argparse surface | LOW | `[VERIFIED]` |
| **`editions/desktop/review_ui.py`** | **Direct adapter wiring, 599 LOC** | **Consume composition root** | **HIGH** | Imports `KrakenEngine`, `TesseractEngine`, `GrayscaleProcessor`, `DocumentPageSource`, `VLMEngine`, `SQLiteJobStore` … directly | **HIGH** | `grep -c "from omniocr.composition"` → **0** `[VERIFIED]` |
| `editions/server/composition.py` | Own wiring | Consume core root | **HIGH** | same | **HIGH** | → **0** `[VERIFIED]` |
| `editions/cloud/composition.py` | Own wiring | Consume core root | **HIGH** | same | **HIGH** | → **0** `[VERIFIED]` |

### The finding that matters most

`[VERIFIED]` **All three editions bypass the composition root.** `omniocr/composition/desktop.py` exists, is correct, and is used by `interfaces/cli.py` — but **no edition imports it**.

This is not a style point. The composition root is the only enforcement site for the wiring invariants: the `RetryingEngine`/`CircuitBreakerEngine` decorators, the VLM grounding guard, the script→lexicon mapping, and the new Kraken GPU→CPU fallback are all applied inside `create_ensemble_pipeline()`. An edition that constructs `KrakenEngine` directly receives **none of them** unless it reimplements each. `[HYPOTHESIS — not traced call-by-call through every edition]` this is the most probable source of behavioural divergence between the CLI and the Streamlit UI.

---

## Phase 3 — Dependency and Coupling Analysis

### Circular dependencies
`[VERIFIED]` **None between layers** — `infrastructure → application` and `ports → {application, infrastructure}` both scan to zero.
`[UNKNOWN]` intra-`infrastructure/` cycles across its 26 modules were not graphed; no SCC analysis was run.

### Layer leaks
`[VERIFIED]` **Core: clean.** No infrastructure import reaches `application/` or `domain/`. (The prior audit's two leaks were genuinely fixed.)
`[VERIFIED]` **Editions: leaking.** `review_ui.py` imports 12 infrastructure modules directly into a UI, violating `CLAUDE.md` rule 4 — "No engine/OpenCV/DB calls in UI or API controllers".

### Shared mutable state
`[VERIFIED]` `KrakenEngine._model` and `._device` mutate lazily on first use; `._device` is *reassigned* on GPU→CPU fallback. Under ADR-003 page parallelism two threads can enter `_recognize` on one shared instance.
`[HYPOTHESIS]` the `_model` race is likely benign — worst realistic outcome is a duplicated load, since both writes store equivalent objects.
`[UNKNOWN]` **whether Kraken's `TorchSeqRecognizer` is thread-safe for concurrent `rpred` calls.** Not tested; ADR-003 does not address it. **Highest-value unverified risk in the system**, because parallelism is the intended remedy for the performance problem.

### Tight coupling hotspots
`[VERIFIED]` `pipeline.py` — highest afferent coupling (all editions + CLI), moderate efferent (12 port imports, all abstract). Expected for an orchestrator.
`[VERIFIED]` `domain/models.py` — imported everywhere; appropriate, it is the shared language and it is pure.

### Boundary violations
`[VERIFIED]` Editions → infrastructure (above).
`[VERIFIED]` **Licence boundary is mechanically enforced** — `scripts/check_license_isolation.py` is a CI gate preventing GPLv3 `calamari_ocr` from reaching core. Policy encoded as a gate rather than a doc; stronger than most codebases achieve.

---

## Phase 4 — AI Orchestrator Review

**Applicability: partial.** OmniOCR is an OCR pipeline, not an agent framework, but contains one LLM integration (`infrastructure/vlm.py`, 237 LOC). Agent/multi-model-routing sub-questions are `[N/A]`.

### Orchestration model
`[VERIFIED]` **Centralized, correctly separated.** Routing lives in `application/router.py` behind `IRouter`; the orchestrator never names a provider. Provider specifics are confined to `vlm.py` behind `IOCREngine` — the VLM is *just another engine*, which is exactly why it composes with the same retry/circuit-breaker/caching decorators as Tesseract.

### Async and concurrency
`[VERIFIED]` No async in core ⇒ **sync-blocking-in-async is structurally impossible** there.
`[VERIFIED]` `vlm.py` uses blocking `urllib.request.urlopen(..., timeout=120)`.
`[HYPOTHESIS]` in the FastAPI editions, a VLM-enabled pipeline run inside a request handler rather than the RQ/Celery worker would occupy an event-loop thread for up to 120 s — `CLAUDE.md` rule 6 exists to prevent this. Not traced through edition handlers.
`[VERIFIED]` Bound: `ThreadPoolExecutor(max_workers=...)`, caller-supplied. **No backpressure** — `run()` materializes `list(self._page_source.stream(document))` before submitting.
`[HYPOTHESIS]` that eagerness partially defeats the streaming-ingest intent for very large PDFs; unconfirmed because the 339 MB target document has never been run.

### State and context
`[VERIFIED]` **Explicit, never ambient.** `TenantContext` is threaded as a parameter through every port method. No thread-locals, no globals. This is the single best decision in the codebase — it is what makes page-level parallelism safe at all.
`[VERIFIED]` Job state behind `IJobStore` (`InMemory`, `SQLite`, `Redis`), checkpointed per page.

### Failure semantics
`[VERIFIED]` `RetryingEngine`, `CircuitBreakerEngine`, `CachingEngine` all implement `IOCREngine` — clean Decorator, not special-casing.
`[VERIFIED]` Fallback routing per script with Tesseract fallback in `create_ensemble_pipeline`.
`[VERIFIED]` Partial failure is first-class: a failed page yields `DocumentPage(failures=(PageFailure(...),))` and the run continues; the CLI reports it in `failures[]`.
`[VERIFIED]` Kraken GPU→CPU degradation is permanent-per-instance with a logged warning (`d41bd13`).

### Tool execution / output validation
`[VERIFIED]` **Strongest property in the system.** VLM output is never trusted directly — `GroundingGuard` requires box-overlap against a verifiable engine before VLM text may be used (`extract_guarded` → `(grounded, ungrounded)`). Post-correction is suggest-only: `SuggestOnlyCorrector` emits `Suggestion` objects and never mutates source text. Both property-tested.
`[VERIFIED]` `api_url` scheme validated at construction, rejecting `file://` and custom schemes (`83be0b5`).

### Scalability bottlenecks
`[VERIFIED]` **Single point most likely to fail at 10×: recognition itself on CPU.** Measured **~500 s for one page** at `--engine ensemble`; a 400-page book is ~55 h single-threaded. Nothing else is within two orders of magnitude.
`[VERIFIED]` **Second: checkpoint write amplification** — the entire accumulated `DocumentStructure` is serialized after every page ⇒ O(n²) I/O across a book.
`[VERIFIED]` **Orchestrator is effectively stateless** — holds only injected collaborators; per-run state is local or in the injected `IJobStore`. Caveat: the engine adapters it holds are not (Phase 3).

### Stack-specific
- **FastAPI** — `[UNKNOWN]` background-tasks-vs-worker usage not traced in `editions/{server,cloud}/main.py`. Dedicated workers (RQ/Celery) exist, so the right primitive is available; universal use is unverified.
- **Redis** — `[VERIFIED]` `RedisJobStore` holds job checkpoints, i.e. resumable state, not merely ephemeral cache. `[HYPOTHESIS]` without persistence configured, a restart loses resumability for in-flight jobs; not confirmed against the deployment manifest.
- **Docker** — `[VERIFIED]` only `editions/cloud` is containerized, and its service boundaries (API / worker / Redis) do match `docker-compose.yml`. Desktop and server have no packaging.

---

## Phase 5 — Anti-Pattern Detection

Absence is reported as a finding where it was checked.

| Anti-pattern | Verdict | Evidence |
|---|---|---|
| God module | `[FALSE]` | Largest core module `pipeline.py` = 567 LOC, under the 800 limit; 26 focused infrastructure modules. `review_ui.py` (599) is the largest file overall but is a UI, not a god *service*. |
| Hidden monolith | `[FALSE]` | Editions are genuinely separable; core is a library, not a disguised service mesh. |
| Shared database coupling | `[FALSE]` | SQLite stores are per-concern (`jobs`, `corrections`) and reached only through ports. |
| Temporal coupling | `[HYPOTHESIS]` | `KrakenEngine.device`/`._model` resolve on first `extract`; calling `parse_records()` beforehand yields provenance for a device never used. Contained; constructor injection (`device=`) is the escape hatch. |
| Anemic domain model | `[HYPOTHESIS — partial, defensible]` | Logic sits in `application/`, but `GroundTruthLine.from_correction()` as sole constructor and `Confidence`/`BBox` validation put real invariants in the domain. Deliberate functional-core style, not accidental anaemia. |
| Orchestrator bottleneck | `[VERIFIED]` | All paths route through `PipelineOrchestrator`. Mitigated: stateless and page-parallel. Structural, not pathological. |
| Infrastructure leakage into domain | `[FALSE]` core · `[VERIFIED]` **at the edition layer** (12 direct imports in `review_ui.py`). |
| Premature abstraction | `[HYPOTHESIS — mostly justified]` | Single-implementation ports exist (`ITrainer`, `ILineCropper`, `ICorrectionStore`, `IEvaluator`). In hexagonal these are boundary definitions the Dependency Rule requires, and each has test doubles as a second implementer. `IOCREngine` has **7** implementations and `IExporter` **6** — those are load-bearing. |
| Overengineering | `[FALSE]` | Every abstraction traces to a stated requirement (multi-engine voting, faithfulness, licence isolation). |
| Underengineering | `[VERIFIED]` | **Editions lack the composition boundary the core already provides.** The abstraction exists; editions don't use it. |
| Defensive-programming smell | `[VERIFIED]` | `training_orchestrator.py` — 6× `hasattr(result, "value")`. Elsewhere the codebase uses `isinstance(x, Ok)` and lets contract violations surface; here it silently substitutes a zeroed `EvaluationReport`, so a promotion decision could rest on fabricated zeros. |

---

## Phase 6 — Executive Summary

> Everything from here on is the **pre-remediation** 2026-08-02 assessment,
> preserved unedited. For current state see *Remediation — 2026-08-05* above.

### ARCHITECTURE SCORE: 7 / 10 *(pre-remediation; now 9.5)*

Between rubric 8 ("minor drift in 1–2 modules, no critical violations") and 6 ("moderate drift, 1–2 high-severity violations"). The **core** rates 8–9: dependency rule verified clean by scan, ports real, composition root correct, faithfulness enforced structurally rather than by convention. The **edition layer** pulls it down — three modules carrying the same HIGH violation. No CRITICAL violation exists, which excludes 6.

### MATURITY LEVEL: **Early Production**

`[VERIFIED]` CI enforces mypy `--strict`, ruff, format, bandit, licence isolation and an 80% coverage floor, all currently green; a headless CLI with a stable exit-code contract exists. **But** the primary recognizer was provably non-functional until `83be0b5`; the accuracy corpus is synthetic (PIL renders of Arial), so no accuracy claim is evidence-backed; the 339 MB target document has never been processed. Production requires evidence the system reads books, and that evidence does not yet exist.

### PRIMARY RISKS (ranked)

1. `[VERIFIED]` **No evidence of real-world accuracy.** Baselines come from synthetic Arial renders; Kraken measured CER 0.70 vs Tesseract 0.00 on them, which says nothing about real 19c Greek print. Every quality claim is currently unfalsifiable.
2. `[VERIFIED]` **Performance is impractical at book scale** — ~500 s/page ensemble ⇒ ~55 h for 400 pages single-threaded.
3. `[HYPOTHESIS]` **Engine thread-safety under ADR-003 is unverified** — shared `KrakenEngine._model` across workers, Kraken's own safety unknown. This blocks the only realistic mitigation for risk 2.
4. `[VERIFIED]` **Editions bypass the composition root**, so wiring invariants (resilience decorators, grounding guard, GPU fallback) are not guaranteed outside the CLI.
5. `[VERIFIED]` **Checkpoint write amplification is O(n²)** — degrades precisely as documents grow, i.e. in the target use case.

### CRITICAL VIOLATIONS

**None.** Highest severity is HIGH: three instances of the edition composition bypass, plus the router-checkpoint gap.

### REFACTOR URGENCY: **Next Sprint**

The core architecture is sound and needs no restructuring — worth stating plainly, since it is unusual. What is urgent is *evidence*, not redesign: real scanned fixtures and one full run of the target document, because until those exist nothing else can be prioritized on facts. The edition composition bypass belongs in the same sprint, before further edition features accrete against direct adapter imports.

---

## Phase 7 — Refactoring Roadmap

### IMMEDIATE (before next feature)

- **[Phase 3 — shared mutable state]** → Verify `KrakenEngine` thread-safety under `ThreadPoolExecutor`; if unsafe, one engine per worker or a lock around `_load_model` → parallelism becomes usable, unblocking the only realistic fix for 500 s/page.
- **[Phase 5 — defensive unwrapping]** → Replace the 6 `hasattr(x, "value")` sites in `training_orchestrator.py` with `isinstance(x, Ok)` + explicit propagation → a broken evaluation fails loudly instead of promoting against fabricated zero-CER reports.
- **[Phase 2 — `RegistryAwareRouter`]** → Add `checkpoint: Path` to `PromotedModel`, thread it through `IModelRegistry`, resolve it in the router → fine-tuned models actually get used; today promotion is a no-op at inference.

### HIGH-IMPACT (next sprint)

- **[Phase 6 — risk 1]** → Replace the synthetic corpus with ≥3 real scanned pages per script variety plus diplomatic ground truth; recompute baselines; drop the `xfail` on `test_kraken_beats_tesseract_on_hard_scripts` → the accuracy gate becomes meaningful and the Kraken-vs-Tesseract question gets a real answer.
- **[Phase 6 — risk 1]** → Run the full `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf`; record peak RSS, wall time, pages/min and kill-and-resume equivalence in `docs/RUNBOOK.md` → converts three unproven design claims into measurements.
- **[Phase 2 — edition bypass]** → Refactor `editions/{desktop,server,cloud}` onto `omniocr.composition`; extract a view-model from `review_ui.py` in the same pass → restores `CLAUDE.md` rule 4 and makes wiring invariants hold everywhere.
- **[Phase 4 — checkpoint amplification]** → Checkpoint every N pages (default 25) before considering an append-semantics port → removes the O(n²) I/O with the smaller diff; revisit only if the real-document run proves it insufficient.

### LONG-TERM (architectural evolution)

**Target state: the current architecture.** It does not need replacing — it needs its own rules applied at the edges. Two additive evolutions only:

1. **`ILineRecognizer` port** — `IOCREngine.extract(page)` forces every recognizer to redo layout, so segmentation runs twice per page and Kraken's baseline polygons never reach the recognizer. An additive line-level port with orchestrator feature-detection removes the duplicate pass. **Gate on measurement from the real-document run; do not build speculatively.**
2. **Metrics export** — the `IEventBus` Observer seam already exists; `prometheus-client` in the server/cloud extras with a no-op desktop sink is wiring, not new architecture.

**Migration sequence (dependency-ordered):**
1. thread-safety verification → 2. real corpus → 3. real-document run → 4. edition composition refactor → 5. checkpoint batching → 6. training-loop completion → 7. `ILineRecognizer` *(only if step 3 justifies it)*.

Steps 1–3 are evidence-gathering with near-zero regression risk. Step 4 is the only multi-module change — mitigate with one commit per edition and a full suite run between. Step 7 is the sole port change: requires an ADR and a contract-equivalence test across all `IOCREngine` adapters.

### SWITCHING TRIGGERS

- `[HYPOTHESIS]` **GPU becomes the default target.** CPU-only is load-bearing in `docs/ARCHITECTURE.md`. GPU-first would favour batched line-level inference, making `ILineRecognizer` mandatory rather than optional, and would reopen `blla.segment` (currently rejected: 6.1 GB allocation on an 800×180 page).
- `[HYPOTHESIS]` **Concurrent multi-tenant volume.** `TenantContext` threading already anticipates it, but `InMemoryJobStore` defaults and per-process engine instances would need a pooled model server.
- `[VERIFIED]` **Manuscript/handwritten scope.** `infrastructure/htr/` is an empty scaffold and the locked scope is printed material; HTR needs a different recognition bank and a different faithfulness model.
- `[HYPOTHESIS]` **Interactive per-page latency requirement.** At ~500 s/page nothing interactive is possible; that forces GPU plus a persistent warm-model service — a service architecture rather than a library.

---

## Appendix — Verification method

`[VERIFIED]` claims rest on commands run against `b0e06ba`:

```bash
grep -n "from omniocr.infrastructure" packages/omniocr/src/omniocr/application/*.py
```

```bash
grep -c "from omniocr.composition" editions/desktop/review_ui.py
```

```bash
grep -rn "async def\|await \|asyncio" packages/omniocr/src/omniocr/ | wc -l
```

`[HYPOTHESIS]` marks structural inference with no runtime observation. `[UNKNOWN]` marks evidence deliberately not collected — intra-`infrastructure` cycle analysis, FastAPI handler tracing in the server/cloud editions, and Redis persistence configuration. Nothing was fabricated to close a gap.
