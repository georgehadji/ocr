# OmniOCR Implementation Plan

**Date:** 2026-07-27
**Source:** Code Review & Performance Audit + UX Optimization Plan
**Status:** Living document — update with each delivery milestone

---

## 1. Executive Summary

OmniOCR is a production-grade OCR suite for printed Greek across 6 varieties (modern monotonic, polytonic, Ancient, Byzantine, Pontian, mixed). The codebase comprises 36 source files (~3,244 lines) in a clean hexagonal architecture with 136 passing tests at 87% coverage. Two parallel analyses — a 9-dimension code review & performance audit and a 3-sprint UX optimization plan — identified 13 actionable fixes and 10 UX enhancements.

This plan consolidates both streams into a prioritized, dependency-aware implementation roadmap targeting full delivery within ~10 working days.

**Key metrics at baseline:**
- 136 tests, 87% coverage, ruff clean, mypy strict
- 6 export formats, 3 editions, 4 fixture varieties
- AWS/CPU-only, 2 GB upload ceiling, faithfulness-gated CI

---

## 2. Current Architecture Assessment

### 2.1 Strengths

| Area | Assessment |
|---|---|
| **Hexagonal architecture** | 6 clean layers (domain → ports → application → infrastructure → composition → testing). No inward dependency violations. |
| **Faithfulness enforcement** | Frozen dataclasses, suggest-only corrections, CI-gated faithfulness test, grounding guard for VLM output. |
| **Test pyramid** | 136 tests across unit, property, contract, E2E, and regression layers. 87% coverage with `--cov-fail-under=80` CI gate. |
| **CI/CD pipeline** | 4-config matrix (Win+Linux × Python 3.11/3.12), ruff, mypy strict, bandit, pip-audit, license isolation, CER regression gate. |
| **Optional dependency model** | 12 extras (pdf, kraken, tesseract, vlm, calamari, etc.) keep the base install lean and license-clean. |
| **Observability foundation** | structlog configured, per-page timing, event bus with pipeline lifecycle events. |
| **Security posture** | Upload validation (magic bytes, path traversal), secrets masked in settings, GPLv3 liability isolated. |

### 2.2 Technical Debt & Architecture Risks

| Risk | Severity | Source |
|---|---|---|
| Single-threaded page rendering bottlenecks pipeline latency at scale | High | Audit S-01 |
| `api_key` exposed in VLMEngine repr bypasses Settings masking | Medium | Audit X-01 |
| `CachingEngine` leaks across document boundaries in Cloud workers | Medium | Audit M-01 |
| `CircuitBreakerEngine`/`CachingEngine` lack locking for concurrent use | Medium | Audit P-01 |
| `InMemoryJobStore` default causes silent data loss on worker restart | Medium | Audit L-02 |
| `SuggestOnlyCorrector` co-located with `PipelineOrchestrator` in 500-line file | Medium | Audit O-01 |
| `configure_logging()` never called by edition composition roots | Medium | Audit V-01 |
| `count_pages()` re-streams document, doubling I/O | Medium | Audit R-01 |

### 2.3 Completed UX Sprints

| Sprint | Features | Status |
|---|---|---|
| Sprint 1 | Real-time progress, session save/load, batch accept, export | ✅ Done |
| Sprint 2 | Edit mode toggle, text search, dashboard overview | ✅ Done |
| Sprint 3 | Dark mode, BBox overlay, engine comparison, searchable PDF | ✅ Done |

---

## 3. Detailed Implementation Plan

### 3.1 Priority Roadmap

```
SPRINT A (Quick Wins — 1 day)         SPRINT B (Strategic — 3 days)
├── A1: X-01 API key masking          ├── B1: S-01 Page-level parallelism
├── A2: V-01 Wire logging config      ├── B2: M-01 TTL cache eviction
├── A3: R-01 count_pages caching      ├── B3: L-01/L-02 Job store hardening
└── A4: C-02 Polytonic selectbox      └── B4: P-01 Resilience locking

SPRINT C (Maintenance — 2 days)       SPRINT D (Documentation — 1 day)
├── C1: O-01 Extract corrector        ├── D1: Architecture Decision Records
├── C2: R-03 run_iteratively cleanup  ├── D2: Operator runbooks
├── C3: S-02 Image decode cache       └── D3: Post-implementation validation
└── C4: M-02 PDF memory release
```

---

### 3.2 Sprint A — Quick Wins (1 day)

#### A1: Mask VLM API key in repr

| Field | Detail |
|---|---|
| **Objective** | Prevent API key leakage when VLMEngine is logged or printed |
| **Source** | Audit X-01 — `vlm.py:69` |
| **Affected components** | `infrastructure/vlm.py` (add `__repr__`), tests unchanged |
| **Design change** | Add `__repr__` method that truncates `_api_key` to first 8 + last 4 chars |
| **Implementation tasks** | 1 line — `def __repr__(self) -> str:` with masking |
| **Testing** | Existing VLM tests verify no behavior change. Add `test_vlm_repr_masks_api_key` |
| **Acceptance criteria** | `repr(VLMEngine("sk-or-v1-abcdef123456"))` shows `"...abcf"` not `"sk-or-v1-abcdef123456"` |
| **Rollback** | Remove the `__repr__` method; `repr()` falls back to default |

#### A2: Wire logging configuration

| Field | Detail |
|---|---|
| **Objective** | Ensure structured logging is active in all editions |
| **Source** | Audit V-01 — `logging.py:12-38` |
| **Affected components** | `editions/desktop/review_ui.py`, `editions/server/main.py`, `editions/cloud/main.py` |
| **Design change** | Call `configure_logging()` at module level in each edition entry point |
| **Implementation tasks** | 1 import + 1 call per entry point (3 files, ~6 lines total) |
| **Testing** | Verify `pipeline_start`, `page_completed` events appear in logs |
| **Acceptance criteria** | Structured log events emitted when running `streamlit run`, `uvicorn`, or `celery worker` |
| **Rollback** | Remove the `configure_logging()` call; it's a no-op if never called |

#### A3: Cache PDF page count

| Field | Detail |
|---|---|
| **Objective** | Avoid re-streaming the document just to count pages |
| **Source** | Audit R-01 — `pipeline.py:361-370` |
| **Affected components** | `application/pipeline.py` — `count_pages()` method |
| **Design change** | Try `fitz.open().len` for PyMuPDF; fall back to streaming count |
| **Implementation tasks** | Replace `sum(1 for _ in stream(...))` with `fitz.open(stream=doc, filetype="pdf").len` inside a `try/except (ImportError, TypeError)` guard |
| **Testing** | Existing E2E test covers `count_pages()`. Add `test_count_pages_uses_fitz_length` |
| **Acceptance criteria** | `count_pages(pdf_bytes)` returns correct count without full stream traversal when PyMuPDF is available |
| **Rollback** | Revert to streaming count — functionally correct, just slower |

#### A4: Replace polytonic popover with selectbox

| Field | Detail |
|---|---|
| **Objective** | Eliminate O(n) widget inflation in the per-line polytonic keyboard |
| **Source** | Audit C-02 — `review_ui.py:499-503` |
| **Affected components** | `editions/desktop/review_ui.py` — line display loop |
| **Design change** | Replace 58 `st.button()` calls with 1 `st.selectbox()` that shows the glyph on selection |
| **Implementation tasks** | Remove the `for combo, glyph in POLYTONIC_MAP.items()` button loop; use `st.selectbox("Character", [...])` with a single `st.button("Insert")` or `st.write(glyph)` on change |
| **Testing** | Manual UI verification — Streamlit components cannot be unit-tested directly |
| **Acceptance criteria** | Polytonic keyboard popover renders in < 100ms for a page with 100 lines (vs. ~2s before) |
| **Rollback** | Revert to the button-loop approach |

---

### 3.3 Sprint B — Strategic (3 days)

#### B1: Page-level parallelism with ThreadPoolExecutor

| Field | Detail |
|---|---|
| **Objective** | Reduce wall-clock time for multi-hundred-page books |
| **Source** | Audit S-01 — sequential page rendering |
| **Affected components** | `application/pipeline.py` — `run()` and `run_iteratively()` methods; `infrastructure/ingest.py` — DocumentPageSource |
| **Design change** | Add an optional `max_workers` parameter to PipelineOrchestrator. When set, `_process_page()` runs in a ThreadPoolExecutor for each page. The executor wraps the CPU-bound OCR (Tesseract/Kraken are CPU-bound but release the GIL via C extensions). Results are collected in page-number order before yielding. The per-page failure isolation is preserved — failed pages are returned with `PageFailure`. |
| **Refactoring** | Extract `_process_page()` call into a `submit`/`future` pattern. Keep synchronous path as default (`max_workers=None`). |
| **Implementation tasks** | 1. Add `max_workers: int | None = None` to `__init__` and `run()`/`run_iteratively()` 2. Use `concurrent.futures.ThreadPoolExecutor` for parallel page processing 3. Collect futures in page-number order 4. Update `run()` and `run_iteratively()` to use the parallel path when `max_workers` is set |
| **Testing** | Add `test_parallel_pipeline_processes_pages_concurrently`. Verify page order is preserved. Verify per-page failure isolation works under parallelism. |
| **Acceptance criteria** | Wall-clock time for a 10-page PDF with `max_workers=4` is ≤ 50% of single-threaded time. Page order in output is correct. |
| **Rollback** | Set `max_workers=None` (default) to use the synchronous code path. |

#### B2: TTL-based cache eviction for CachingEngine

| Field | Detail |
|---|---|
| **Objective** | Prevent cross-document cache leakage in Cloud workers |
| **Source** | Audit M-01 — `resilience.py:87-112` |
| **Affected components** | `infrastructure/resilience.py` — CachingEngine class |
| **Design change** | Add optional `ttl` (time-to-live, in seconds) parameter. On cache hit, check if the entry has expired. On miss, evict expired entries before inserting new ones. Use `time.monotonic()` for TTL tracking (consistent with CircuitBreakerEngine). Entries store `(timestamp, value)` tuples. |
| **Implementation tasks** | 1. Add `ttl: int | None = None` to `__init__` 2. Cache entries become `dict[str, tuple[float, tuple[OCRBlock, ...]]]` 3. On `extract()`, check TTL before returning the cache hit 4. On insert after `max_size` is exceeded, evict oldest non-expired entry first, then expired entries |
| **Testing** | Add `test_caching_engine_ttl_expiry` using injectable clock (same pattern as CircuitBreakerEngine) |
| **Acceptance criteria** | Cache entry inserted with TTL=1 and retrieved after 2 seconds returns miss |
| **Rollback** | Set `ttl=None` (default) for existing behavior |

#### B3: Job store hardening

| Field | Detail |
|---|---|
| **Objective** | Prevent silent data loss in Server/Cloud editions |
| **Source** | Audit L-01, L-02 — `jobs.py`, `pipeline.py:287` |
| **Affected components** | `infrastructure/jobs.py`, `application/pipeline.py`, `editions/server/composition.py`, `editions/cloud/composition.py` |
| **Design change** | 1. Change `PipelineOrchestrator.__init__` default `job_store` from `None` (→ InMemoryJobStore) to `InMemoryJobStore()` — same behavior, explicit default. 2. In Server/Cloud composition roots, wire `SQLiteJobStore("omniocr_jobs.db")` instead of relying on the pipeline default. 3. Add a `RedisJobStore` adapter for Cloud deployments — stores checkpoints in Redis via `rq` or raw `redis-py`. |
| **Implementation tasks** | 1. Create `RedisJobStore` in `infrastructure/jobs.py` 2. Update `create_server_pipeline()` and `create_cloud_pipeline()` to use explicit job stores 3. Add `test_redis_job_store` (skipped if Redis not available) |
| **Testing** | Existing job store tests cover InMemoryJobStore and SQLiteJobStore. Add RedisJobStore tests gated by `pytest.importorskip("redis")`. |
| **Acceptance criteria** | Server pipeline uses SQLiteJobStore by default. Cloud pipeline uses RedisJobStore. Worker restarts resume from checkpoint. |
| **Rollback** | Remove the explicit job_store wiring from composition roots |

#### B4: Add locking to resilience engines

| Field | Detail |
|---|---|
| **Objective** | Make CircuitBreakerEngine and CachingEngine safe for concurrent use |
| **Source** | Audit P-01 — `resilience.py:55,92` |
| **Affected components** | `infrastructure/resilience.py` — CircuitBreakerEngine, CachingEngine |
| **Design change** | Add optional `lock` parameter to `__init__`. When provided, use it to guard state mutations (CircuitBreaker: `_failures`, `_opened_at`; Cache: `OrderedDict` operations). When not provided, current behavior is preserved (single-threaded safe). Default: `threading.Lock()`. |
| **Implementation tasks** | 1. Add `lock: Lock | None = None` to both `__init__` methods 2. Default to `threading.Lock()` 3. Guard all state mutations with `with self._lock:` |
| **Testing** | Add `test_circuit_breaker_thread_safety` — launch N threads, verify only 1 passes the circuit after all fail. |
| **Acceptance criteria** | 10 concurrent threads hitting a CircuitBreakerEngine with threshold=1 all see the circuit open after the first failure. No more than 1 extraction succeeds during the half-open window. |
| **Rollback** | Pass `lock=None` to disable locking |

---

### 3.4 Sprint C — Maintenance (2 days)

#### C1: Extract SuggestOnlyCorrector to application/post_correction.py

| Field | Detail |
|---|---|
| **Objective** | Improve discoverability and test isolation |
| **Source** | Audit O-01 — `pipeline.py` |
| **Affected components** | `application/pipeline.py` (remove class), new `application/post_correction.py` (add class), all imports updated |
| **Implementation tasks** | 1. Create `application/post_correction.py` 2. Move `SuggestOnlyCorrector` class 3. Update imports in `pipeline.py`, `test_core.py`, `composition/desktop.py` |
| **Testing** | All 136 existing tests must pass after extraction |
| **Rollback** | Move class back to pipeline.py |

#### C2–C4: Additional maintenance items

| ID | Task | File(s) |
|---|---|---|
| C2 | Document `run_iteratively()` error behavior (stream failures yield nothing) | `pipeline.py` |
| C3 | Cache decoded PIL images in session state for faster page navigation | `review_ui.py` |
| C4 | Release source PDF memory after export in SearchablePdfExporter | `exporters.py` |

---

## 4. Task Breakdown Structure (WBS)

```
OmniOCR Implementation Plan
├── Sprint A — Quick Wins (1 day)
│   ├── A1: X-01 VLM api_key masking ─── 0.5h
│   ├── A2: V-01 Wire logging config ─── 0.5h
│   ├── A3: R-01 count_pages caching ─── 1h
│   └── A4: C-02 Polytonic selectbox ─── 2h
├── Sprint B — Strategic (3 days)
│   ├── B1: S-01 Page-level parallelism ─── 8h
│   ├── B2: M-01 TTL cache eviction ─── 4h
│   ├── B3: L-01/L-02 Job store hardening ─── 6h
│   └── B4: P-01 Resilience locking ─── 6h
├── Sprint C — Maintenance (2 days)
│   ├── C1: O-01 Extract post_correction.py ─── 4h
│   ├── C2: R-03 run_iteratively doc fix ─── 1h
│   ├── C3: S-02 Image decode cache ─── 3h
│   └── C4: M-02 PDF memory release ─── 2h
└── Sprint D — Documentation (1 day)
    ├── D1: ADRs for architectural decisions
    ├── D2: Operator runbooks for production
    └── D3: Post-implementation validation
```

---

## 5. Risk & Mitigation Matrix

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| ThreadPoolExecutor introduces non-determinism in page ordering | Medium | High | Collect futures in order; add ordering assertion to test |
| TTL cache eviction conflicts with existing LRU eviction | Low | Medium | TTL takes priority — expire before LRU eviction. Test both paths. |
| `count_pages()` caching breaks for non-PyMuPDF sources | Low | Low | `try/except ImportError` falls back to streaming count |
| Polytonic selectbox degrades UX for power users who type combos | Low | Low | Keep the grouping structure; show combo → glyph mapping in the selectbox options |
| RedisJobStore adds Redis dependency to Cloud edition | Medium | Low | Cloud edition already depends on Redis for Celery broker; no new dependency |
| Extraction of SuggestOnlyCorrector breaks subtle import cycles | Low | Medium | Run full test suite + mypy before and after extraction |

---

## 6. Testing & Quality Assurance Strategy

| Layer | Existing | After Sprint A | After Sprint B | After Sprint C |
|---|---|---|---|---|
| Unit tests | 74 | +2 (X-01 repr, count_pages) | +4 (TTL, locking, parallelism, Redis) | No change (refactor only) |
| Property tests | 10 | No change | No change | No change |
| Contract tests | 24 | No change | No change | No change |
| E2E smoke | 4 | No change | +1 (parallel pipeline) | No change |
| Regression gate | 8 | No change | No change | No change |

**CI/CD:** All sprints maintain the existing CI gates (80% coverage, ruff, mypy strict, bandit, pip-audit, license isolation, CER regression). No gate relaxation.

**Code review:** Each sprint produces a PR against `main`. Review checklist:
1. Does the fix break the causation chain, or mask the symptom?
2. Are all `[VF]` claims backed by executable tests?
3. Does any new code introduce a layer violation?
4. Is the faithfulness invariant preserved?

---

## 7. Deployment & Rollback Plan

Each sprint deploys via `git push` → CI → merge to `main`. No database migrations, no infrastructure changes for Sprints A, C, D.

**Sprint B deployment considerations:**
- B1 (parallelism): Controlled by `max_workers` parameter. Default `None` = synchronous (current behavior). Deploy with `max_workers=2` for canary, then scale up.
- B2 (TTL): Controlled by `ttl` parameter. Default `None` = no TTL (current behavior).
- B3 (job store): Server/Cloud composition roots updated to explicit stores. Existing callers that construct `PipelineOrchestrator` manually are unaffected.
- B4 (locking): Default lock = `threading.Lock()`. Negligible overhead in single-threaded use.

**Rollback strategy:** All changes are additive (new parameters with safe defaults) or internal refactoring (extraction). Revert the commit to return to baseline behavior.

---

## 8. Post-Implementation Validation Checklist

- [ ] 136+ tests passing with ≥87% coverage
- [ ] `ruff check` and `mypy --strict` clean
- [ ] `VLMEngine.__repr__` masks the API key
- [ ] `streamlit run editions/desktop/review_ui.py` shows structured logs on startup
- [ ] `count_pages()` returns correct result without re-streaming for PDF inputs
- [ ] Polytonic keyboard popover renders <100ms for 100-line pages
- [ ] `max_workers=4` reduces wall-clock time by ≥40% for 10-page PDF
- [ ] TTL cache eviction works with injectable clock
- [ ] Server/Cloud pipelines use persistent job stores
- [ ] CircuitBreaker threadsafe under 10 concurrent callers
- [ ] SuggestOnlyCorrector imports from `application/post_correction.py`
- [ ] All export formats produce valid output with ground-truthed text
