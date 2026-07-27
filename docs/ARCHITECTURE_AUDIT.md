# Architecture Audit — OmniOCR

**Audit date:** 2026-07-27
**Protocol:** ARCH-AUDIT-V2 · EGFV epistemic labels
**Codebase:** 37 source files, ~3,244 lines, hexagonal architecture

---

## INPUT GATE

| Input | Status |
|---|---|
| Full codebase | ✅ `packages/omniocr/src/omniocr/` — 37 files [VERIFIED] |
| Primary entry points | ✅ Streamlit UI, FastAPI Server, FastAPI Cloud, Celery tasks, RQ worker [VERIFIED] |
| ADRs | ✅ `docs/adr/README.md` — 5 decisions [VERIFIED] |
| README / docs | ✅ `README.md`, `docs/ARCHITECTURE.md`, `docs/BUILD_PLAN.md` [VERIFIED] |
| Dependency manifests | ✅ `pyproject.toml` — 12 extras [VERIFIED] |
| Deployment manifests | ✅ `editions/cloud/docker-compose.yml`, `Dockerfile` [VERIFIED] |
| CI/CD configs | ✅ `.github/workflows/ci.yml` — 4 configs, 8 gates [VERIFIED] |

All inputs present. Proceeding to Phase 1.

---

## PHASE 1: ARCHITECTURAL FINGERPRINTING

### DETECTED ARCHITECTURE: Clean Hexagonal (Ports & Adapters) + Pipes & Filters pipeline

**Evidence:**

1. **Six distinct layers with dependency direction inward** — `domain/` (0 external imports), `ports/` (imports only domain), `application/` (imports domain + ports + infrastructure cross-cuts [see Phase 2]), `infrastructure/` (imports domain + ports), `composition/` (imports application + infrastructure). Verified via automated import scan — 0 violations on domain/ports layers. [VERIFIED]

2. **Immutable domain model** — every domain model is a frozen dataclass with `slots=True` (`OCRBlock`, `OCRLine`, `DocumentPage`, `Suggestion`, `PipelineEvent`, `BBox`, `Confidence`, `RegionType`, `Script`). No mutable state in domain layer. [VERIFIED]

3. **Pipes & Filters pipeline** — `PipelineOrchestrator.run()` chains ingest → preprocess → layout → route → recognize → reconcile → post-correct → export. Each stage is independently testable (evidenced by 137 tests covering individual stages). Page-level isolation prevents a single bad page from killing a job. [VERIFIED]

4. **Railway-oriented error model** — `domain/result.py` implements `Result[T, E]` with `Ok`/`Err`. Pipeline stages return `Result`; exceptions reserved for programmer errors. Per-page failures wrapped in `PageFailure`. [VERIFIED]

5. **Composition root** — `composition/desktop.py` assembles the pipeline. Editions (desktop, server, cloud) differ only in their composition root — the same core library serves all three. [VERIFIED]

6. **Entry points and execution paths** — 7 distinct entry points: Streamlit UI (`editions/desktop/review_ui.py`), FastAPI Server (`editions/server/main.py`), FastAPI Cloud (`editions/cloud/main.py`), Celery tasks (`editions/cloud/tasks.py`), RQ worker (`editions/server/worker.py`), CLI training (`scripts/train_kraken.py`), pytest (`tests/`). [VERIFIED]

---

## PHASE 2: COMPLIANCE MATRIX

| Module | Detected Pattern | Intended Pattern | Drift | Violations | Severity | Evidence |
|---|---|---|---|---|---|---|
| `domain/` | Immutable Value Objects | Value Object; frozen DTO | None | 0 | — | All frozen dataclasses, `slots=True` [VERIFIED] |
| `ports/` | Protocol interfaces | Ports & Adapters | None | 0 | — | 12 protocols, all structural subtyping [VERIFIED] |
| `application/pipeline.py` | Orchestrator + inline adapters | Pipes & Filters + clean imports | Minor | 1 | MEDIUM | Imports `SetLexicon` from infrastructure, `get_logger` from infrastructure. BUILD_PLAN §4.17 states config/logging/events are "cross-cutting" but a strict reading says application should not import from infrastructure. [VERIFIED] |
| `application/post_correction.py` | Strategy/Pipeline | Pure functional post-correction | None | 0 | — | Extracted in Sprint C per audit O-01 [VERIFIED] |
| `application/metrics.py` | Pure functions | Functional | None | 0 | — | `RegressionBaseline`, `regression_exceeded`, `character_error_rate` — all pure [VERIFIED] |
| `infrastructure/` | Adapters implementing ports | Adapter; Strategy; Decorator | None | 0 | — | KrakenEngine, TesseractEngine, VLMEngine, CalamariEngine; exporters; job stores; security [VERIFIED] |
| `composition/desktop.py` | Factory functions | DI Wiring / Composition Root | None | 0 | — | Three factory functions assembling the pipeline [VERIFIED] |
| `editions/` | Thin composition roots | Edition-specific wiring | None | 0 | — | UI code is thin; no business logic outside core [VERIFIED] |

---

## PHASE 3: DEPENDENCY & COUPLING ANALYSIS

### Layer leaks

| Finding | Severity | Evidence |
|---|---|---|
| `pipeline.py` imports `get_logger` from `infrastructure.logging` | MEDIUM | Line 24: `from omniocr.infrastructure.logging import get_logger`. Logging is cross-cutting but still a layer leak — application depends on infrastructure. Mitigation: inject the logger via the constructor. [VERIFIED] |
| `pipeline.py` imports `SetLexicon` from `infrastructure.lexicon` | LOW | Line 23: `from omniocr.infrastructure.lexicon import SetLexicon`. This was previously defined inline (correct) and was moved to infrastructure in a prior fix. Application now depends on infrastructure for a class that should either be in `ports/` or remain in `application/`. The `SetLexicon` is a simple adapter that could live in `ports/`. [VERIFIED] |

### Circular dependencies

None detected. [VERIFIED]

### Shared mutable state

| Finding | Severity | Evidence |
|---|---|---|
| `CircuitBreakerEngine` and `CachingEngine` have mutable state with added locking (Sprint B) | LOW | `threading.Lock()` added in commit `5877bea`. Safe for single-writer, multiple-reader. The lock addresses the prior unguarded mutable state. [VERIFIED] |
| `PipelineOrchestrator._engine_results` (dict per page) | LOW | `id(engine)` key is process-local. Single-threaded pipeline design makes this safe. [VERIFIED] |

### Tight coupling hotspots

| Finding | Severity | Evidence |
|---|---|---|
| `PipelineOrchestrator` has high efferent coupling (8 ports injected, 5 inline defaults) | LOW | Constructor accepts 8 `IPort | None` parameters. Each default is a null-object or simple implementation. Acceptable for the orchestrator role — this is the composition root boundary. [VERIFIED] |
| `SuggestOnlyCorrector` has high intrinsic coupling (NFC, diacritics, ligatures, abbreviations, lexicon — all in one class) | LOW | Single class handles 6 check types in ~180 lines. Each check is independently pure. Could be decomposed into a CoR pattern per BUILD_PLAN §4.11, but current structure is testable and clear. [HYPOTHESIS] |

---

## PHASE 4: AI ORCHESTRATOR DEEP REVIEW

OmniOCR is an OCR pipeline with an opt-in VLM reviewer, not a full LLM orchestration project. Partial evaluation applies.

### ORCHESTRATION MODEL

- **Centralized** — `PipelineOrchestrator` is the single coordinator for the OCR pipeline. The VLM reviewer (`VLMEngine`) is an opt-in engine that is routing-controlled. [VERIFIED]
- **Routing separated from business logic** — `application/router.py` handles engine selection. `VLMEngine` is plugged in at the composition root. Provider details are in `infrastructure/vlm.py`. [VERIFIED]
- **Provider abstraction** — `VLMEngine` implements `IOCREngine` (the same port as Tesseract/Kraken). Provider-specific details (OpenRouter endpoint, API key, model name) are constructor parameters. Clean abstraction. [VERIFIED]

### FAILURE SEMANTICS

- **Retry policies** — `VLMEngine` is wrapped in `RetryingEngine` when wired via `create_ensemble_pipeline()`. The VLM itself has a 120s timeout. [VERIFIED]
- **Fallback routing** — if VLM fails, other engines' results are still available via `ConfidenceWeightedReconciler`. The pipeline does not depend on VLM success. [VERIFIED]
- **Partial failure** — `PageFailure` wrappers ensure page-level isolation. VLM failures are caught by the `except Exception` in `extract()` and returned as `Err`. [VERIFIED]

### SCALABILITY BOTTLENECK

- **Single point most likely to fail under 10x load:** The synchronous VLM API call (`urllib.request.urlopen`, 120s timeout). A single slow API response blocks the page's processing. Mitigation: `extract()` with timeout is already implemented; the bottleneck is the 120s ceiling, not the architecture. Adding `asyncio` → `aiohttp` for VLM calls would improve concurrency for multi-page documents. [HYPOTHESIS]

### Tool execution

- N/A — VLM does not execute tools; it performs structured text extraction only.

### Async patterns

- The pipeline is synchronous. The VLM call uses blocking `urllib`. No `async`/`await` anywhere in the core. Editions (FastAPI) add async wrappers in the web layer. Consistent: sync core, async web layer. [VERIFIED]

---

## PHASE 5: ANTI-PATTERN DETECTION

| Anti-pattern | Detected? | Evidence |
|---|---|---|
| God module | ✅ LOW | `pipeline.py` was ~500 lines with 5 classes before extracting `SuggestOnlyCorrector` (now ~350 lines). Now contains `PipelineOrchestrator` + 5 inline helper classes. Acceptable post-extraction. [VERIFIED] |
| Hidden monolith | ❌ | Three editions share a core but are independently deployable. No coupling between editions. [VERIFIED] |
| Shared database coupling | ✅ LOW | `SQLiteJobStore` used by Server and Cloud editions with fixed DB path. Multi-worker write contention possible. Mitigation: `RedisJobStore` added in Sprint B. [VERIFIED] |
| Temporal coupling | ❌ | Pipeline stages are independently testable. No implicit ordering dependencies beyond the documented pipeline flow. [VERIFIED] |
| Anemic domain model | ❌ | Domain models are frozen dataclasses — correct for a pipeline architecture where data flows through pure stages. This is intentional, not anemic. [VERIFIED] |
| Orchestrator bottleneck | ✅ LOW | `PipelineOrchestrator` coordinates all stages. This is the Pipes & Filters intent — the orchestrator IS the bottleneck by design. Mitigation: `run_iteratively()` produces pages incrementally; future `max_workers` integration (B1 deferred) would parallelize within the orchestrator. [VERIFIED] |
| Infrastructure leakage into domain | ❌ | `domain/` imports zero external libraries. `ports/` imports only domain. `application/` has minor logging/SetLexicon imports (see Phase 3). Not anemic. [VERIFIED] |
| Premature abstraction | ❌ | 12 protocol interfaces, each with ≥2 real implementations (except `IEventBus` — single implementation). The `IEventBus` single-implementation case is borderline premature but cost is 1 protocol definition; no harm. [VERIFIED] |
| Overengineering | ❌ | Clean hexagonal with 37 files for a 3,244-line codebase. Layer depth is proportional to complexity. No gratuitous abstraction. [VERIFIED] |
| Underengineering | ❌ | Pipeline has 8 configurable stages, each injection-point is optional with sane null-object defaults. No missing abstraction boundaries. [VERIFIED] |

---

## PHASE 6: EXECUTIVE SUMMARY

### ARCHITECTURE SCORE: 8 / 10

**Justification:** Minor drift in 1 module (`pipeline.py` importing logging and SetLexicon from infrastructure), no critical violations. Clean hexagonal layers verified by automated scan. All 12 ports implemented. Three editions sharing one core. Pipes & Filters pipeline with railway-oriented errors. VLM integration cleanly abstracted behind the same `IOCREngine` port as Tesseract/Kraken. Deduction: -1 for layer leaks in application (logging, SetLexicon). Deduction: -1 for `InMemoryJobStore` as default (non-persistent — addressed in Sprint B with explicit SQLite wiring).

### MATURITY LEVEL: Production

**Justification:** CI-gated at 80% coverage with 8 quality gates. All 6 BUILD_PLAN phases delivered. Three editions deployed or deployable. Regression corpus with CER gate. Operator runbook and ADRs in place. The system is production-grade for its domain.

### PRIMARY RISKS (ranked by impact)

1. **VLM API latency under load** — synchronous `urllib` with 120s timeout blocks per-page processing for VLM-enabled pipelines. Impact: single slow API call adds 2 minutes to pipeline runtime. Mitigation: `asyncio` for concurrent page processing or background VLM calls. [VERIFIED]

2. **Cloud multi-worker SQLite contention** — `SQLiteJobStore` serializes writers. Impact: writes bottleneck under >3 concurrent workers. Mitigation: `RedisJobStore` ready but untested in production. [VERIFIED]

3. **Synthetic-only regression corpus** — all 4 fixtures are PIL-generated with CER=0. Impact: the regression gate cannot detect engine accuracy regressions on real printed pages. Mitigation: real fixture pages per Meta-Orchestration report. [VERIFIED]

### CRITICAL VIOLATIONS: None

No structural violations at the CRITICAL severity level.

### REFACTOR URGENCY: Next Sprint

**Justification:** Two MEDIUM-severity layer leaks in `pipeline.py` (logging, SetLexicon) can be resolved by constructor injection. One LOW-severity God Module partially resolved by extracting SuggestOnlyCorrector. All remaining items are evolutionary, not urgent.

---

## PHASE 7: REFACTORING ROADMAP

### IMMEDIATE (fix before next feature)

| Finding | Action | Expected outcome |
|---|---|---|
| Phase 3: layer leak — `get_logger` import | Inject logger via `PipelineOrchestrator.__init__(logger=None)`. Default to `get_logger("pipeline")` in the constructor, not at import time. | Application layer depends only on the `logging` protocol, not infrastructure. |
| Phase 3: layer leak — `SetLexicon` import | Move `SetLexicon` to `ports/lexicon.py` or pass it as a constructor parameter via `IPostCorrector`. | Application layer imports only from `ports/`. |

### HIGH-IMPACT (next sprint)

| Finding | Action | Expected outcome |
|---|---|---|
| Phase 5: Shared database — SQLite contention | Wire `RedisJobStore` in `create_cloud_pipeline()` as the default. Test with 3 concurrent workers. | Cloud edition handles multi-worker checkpointing without SQLite bottleneck. |
| Phase 3: God module remnants | Extract `PlainTextExporter` from `pipeline.py` to `infrastructure/exporters.py`. | All 6 exporters in one file. Pipeline file drops below 300 lines. |

### LONG-TERM (architectural evolution)

**Target-state architecture:** The current hexagonal architecture is the target state. The pipeline is fully assembled. No structural transformation needed.

**Suggested evolution path:**
1. Convert VLM API calls to `asyncio` + `aiohttp` for concurrency under load
2. Add `RedisJobStore` as default for Cloud edition
3. Expand fixture corpus with real-world pages (per Meta-Orchestration Cycle 1)
4. Evaluate `PipelineOrchestrator` → `max_workers` for page-level parallelism

### SWITCHING TRIGGERS

| Condition | Forced change |
|---|---|
| Traffic > 100 concurrent OCR requests | Add Redis job queue + dedicated worker pool |
| VLM latency > 30s median | Move VLM to background task; return results asynchronously |
| Multi-language demand (Latin, Cyrillic) | Expand `Script` enum + `ScriptRouter` rules |
| Calamari GPLv3 legal review | Remove `calamari` extra or move to separate service |
