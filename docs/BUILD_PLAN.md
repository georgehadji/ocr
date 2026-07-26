# OmniOCR — Production Build Plan

Engineering plan of record for building OmniOCR to a production-grade standard.

- **What & why** live in [ARCHITECTURE.md](ARCHITECTURE.md) (scope, engine bank, pipeline, faithfulness principle). This document is not a rewrite of it — read that first.
- **How** lives here: the programming paradigm and design patterns chosen per module, the cross-cutting production concerns, the test/CI strategy, packaging, and a phased delivery plan with acceptance criteria.

Locked scope (from ARCHITECTURE.md §1): **CPU-only · printed material · faithful/diplomatic transcription · Desktop edition primary.**

---

## 1. Definition of "production-grade"

A phase is not "done" until it meets all of these. They are the acceptance backbone for every milestone in §10.

1. **Correct & faithful** — no stage silently alters recognized text (ARCHITECTURE.md §1). Enforced by an audit test, not by convention.
2. **Typed & linted** — `mypy --strict` clean, `ruff` + `black` + `isort` clean, no `Any`/`cast` suppression in domain/application layers.
3. **Tested** — ≥80% line coverage overall; domain and post-correction ≥95%; every engine has an integration test against fixture pages with a **CER/WER regression gate**; every port has a contract test.
4. **Observable** — structured logs, per-page metrics, and end-to-end provenance on every output line.
5. **Resilient** — a single bad page never kills a job; jobs are resumable; external calls (VLM) are retried and circuit-broken.
6. **Reproducible** — engine + model + version + parameters are recorded in output metadata; model artifacts are pinned by hash.
7. **Secure** — no secrets in source; uploads validated; GPLv3 Calamari and its TensorFlow dep isolated in a subprocess; web/API surfaces hardened.
8. **Documented** — public ports have docstrings; each edition has a run/README; changelog kept.

---

## 2. Overarching paradigm: Functional Core, Imperative Shell + Hexagonal + Pipes-and-Filters

Three complementary styles, each owning a different axis of the system:

- **Functional Core, Imperative Shell.** All recognition logic, voting, and post-correction are **pure functions over immutable domain values** — deterministic, trivially testable, no I/O. Side effects (file reads, engine subprocesses, DB writes, network) live only in a thin outer shell. This is the single most important decision for a faithfulness-critical OCR system: the core that decides *what the text is* has no hidden state to corrupt it.
- **Ports & Adapters (Hexagonal).** The core depends on **ports** (abstract interfaces); concrete engines/exporters/queues are **adapters** wired at a **composition root** per edition. Already the intended shape of `app_core` — this plan completes it. Dependencies point inward: `interfaces → infrastructure → application → domain`.
- **Pipes & Filters** for the pipeline itself. Ingest → preprocess → layout → route → recognize → reconcile → post-correct → export is a chain of filters passing immutable DTOs. Filters are independently testable and reorderable; back-pressure and streaming fall out naturally.

**Error model:** railway-oriented. The pipeline threads a `Result[T, PipelineError]` so a per-page failure short-circuits *that page* into a review/dead-letter state without raising through the whole job. Exceptions are reserved for programmer errors and boundary failures, not control flow.

---

## 3. Repository & package topology

Monorepo. **One installable core, thin editions.** The three "Editions" become composition roots that depend on the core package — they add wiring and a UI, never business logic (ARCHITECTURE.md §7).

```
OCR/
├─ pyproject.toml                 # workspace / tool config (uv or hatch)
├─ packages/
│  └─ omniocr/                    # THE shared core — the only place logic lives
│     ├─ pyproject.toml           # extras: [tesseract] [kraken] [paddle] [vlm] [calamari]
│     └─ src/omniocr/
│        ├─ domain/               # pure, no I/O, no third-party engine imports
│        ├─ ports/                # abstract interfaces (the hexagon boundary)
│        ├─ application/          # pipeline orchestration + stages (functional core)
│        ├─ infrastructure/       # adapters: engines, exporters, persistence, config
│        └─ composition/          # factory functions that wire ports→adapters per edition
├─ editions/
│  ├─ desktop/                    # primary: Streamlit/Tauri + in-process worker
│  ├─ server/                     # FastAPI + RQ + static UI
│  └─ cloud/                      # FastAPI + Celery + Redis, multi-tenant
├─ tests/                         # unit / contract / integration / regression corpus
├─ models/                        # pinned .mlmodel / .traineddata (hash-locked, git-lfs)
└─ docs/                          # ARCHITECTURE.md, BUILD_PLAN.md, ADRs
```

The existing `Cloud/Desktop/Server Edition/` trees are migrated into `editions/` and reduced to composition roots; `ocr.py` is retired to `prototype/` once Phase 1 reaches parity.

**Dependency extras** keep the base install lean and license-clean:
`pip install omniocr` → Tesseract + Kraken (permissive). `omniocr[calamari]` and `omniocr[vlm]` pull the heavy/encumbered deps only on explicit opt-in.

---

## 4. Module-by-module design

Each module names its **paradigm** and the **design pattern(s)** that are optimal for it, with a one-line justification. Summary table first, detail below.

| Module | Layer | Paradigm | Primary pattern(s) |
|---|---|---|---|
| Domain models | domain | Immutable / functional | Value Object; frozen DTO |
| Result & errors | domain | Functional (railway) | Result/Either monad |
| Provenance | domain | Immutable | Value Object; Memento |
| Ingest | application | Lazy generators | Iterator; Producer–Consumer; Memento (checkpoint) |
| Preprocess | infrastructure | Functional composition | Strategy; Decorator (filter chain) |
| Layout/segmentation | infrastructure | OOP adapter | Adapter; Strategy |
| Script router | application | Declarative rules | Strategy-selection; Chain of Responsibility; Factory |
| Recognition engines | infrastructure | OOP polymorphism | Strategy; Adapter; Plugin Registry; Facade |
| Engine resilience | infrastructure | Decorator | Decorator; Circuit Breaker; Retry; Proxy (cache) |
| Reconcile / vote | application | Pure functional | Strategy (voting policy); Policy object |
| Post-correction | application | **Pure functional** | Chain of Responsibility; Specification; Pipeline |
| Export | infrastructure | OOP + build | Strategy; Factory; Template Method; Builder |
| Pipeline orchestrator | application | Imperative shell | Pipes & Filters; Mediator |
| Job queue / worker | infrastructure | Command | Command; Repository; Observer |
| Review UI | edition | MV* reactive | MVVM/MVC; Observer |
| Composition roots | composition | Declarative wiring | Abstract Factory; Dependency Injection; Builder |
| Config | infrastructure | Declarative schema | Typed Settings (validated) |
| Observability | infrastructure | Aspect / cross-cut | Decorator; Observer (event bus) |

### 4.1 Domain models — Value Objects, immutable
`OCRBlock`, `OCRLine`, `OCRParagraph`, `DocumentPage`, `DocumentStructure`, plus small value objects `BBox`, `Confidence`, and a `Script` enum. Frozen dataclasses (`slots=True`), no methods with side effects, self-validating in `__post_init__` (e.g. `0 ≤ conf ≤ 100`, non-negative box). Immutability is the guardrail behind "faithfulness": once recognized, a line's text cannot be mutated in place — transformations return new values. Extend the existing `domain.py` (currently `OCRBlock`, `TenantContext`) rather than replace it.

### 4.2 Result & error hierarchy — railway-oriented
A minimal `Result[T, E]` (`Ok`/`Err`) in `domain/result.py`, plus a `PipelineError` hierarchy (`IngestError`, `EngineError`, `LayoutError`, `ExportError`). Stages return `Result`; the orchestrator `map`/`and_then`-chains them. Keeps the happy path linear and diverts failures to review/DLQ without exceptions as control flow. (Adopt the `returns` library only if the team prefers it; a 40-line internal Result honors KISS.)

### 4.3 Provenance — the faithfulness ledger
`EngineRun(engine, model_ref, model_hash, params, timestamp)` and `ModelRef` attached to every `OCRLine`. This is what makes output **auditable and reproducible** (§1.6): an exported page can answer "which engine+model produced this character, at what confidence." Corrections are stored as a **separate `Suggestion` layer** (`suggestions.py`) that references a line by id and never edits it — the Memento-like separation is what enforces suggest-only post-correction structurally, not just by discipline.

### 4.4 Ingest — lazy streaming, checkpointed
PyMuPDF (`fitz`) yields pages as a **generator** at ~300 DPI; the worker consumes in bounded batches (Producer–Consumer) so a 2000-page book never lands in memory at once (ARCHITECTURE.md §3.1). A **Memento** checkpoint (last completed page + partial results) is persisted after each batch via `IJobStore`, making jobs resumable after interruption. Port: `IPageSource.stream(document) -> Iterator[RawPage]`.

### 4.5 Preprocess — composable filters
Pure image transforms behind `IImageProcessor`. A **Decorator/filter chain** composes deskew → denoise → binarize; **Strategy** selects the chain per engine (Sauvola/adaptive for Tesseract; light grayscale retained for Kraken/VLM — ARCHITECTURE.md §3.2). Each filter is `ndarray -> ndarray`, pure and unit-testable. Reuse the working OpenCV code from `ocr.py`/`OpenCVImageProcessor` as the first adapters.

### 4.6 Layout & line segmentation — adapter over Kraken/Paddle
`ILayoutAnalyzer.segment(page) -> list[Region]` with ordered line polygons and region class (main / apparatus / scholia / running head). Kraken baseline segmenter is the default adapter; PaddleOCR PP-Structure an alternate Strategy. Reading order is first-class for critical editions.

### 4.7 Script router — declarative selection
Given a region, choose the engine(s). A **Chain of Responsibility** of rules (heuristics/lightweight classifier → `Script` tag) feeds a **Factory** that returns the engine set; scholars can override per document. Kept in `application` (pure decision logic); no engine imports here, only port handles.

### 4.8 Recognition engines — Strategy + Adapter + Registry, behind a Facade
Every engine implements `IOCREngine.extract(img, context) -> list[OCRBlock]` (ARCHITECTURE.md §4). Each concrete engine is an **Adapter** wrapping a third-party lib; a **Plugin Registry** (entry points) lets extras register `KrakenEngine`, `TesseractEngine`, opt-in `CalamariEngine`/`VLMEngine` without the core importing them. An **EngineBank Facade** presents "recognize this line with the routed engine(s)" to the pipeline. **Calamari** is invoked as a **subprocess adapter** (CLI over a temp dir) — GPLv3 + TensorFlow isolation (ARCHITECTURE.md §4, Licensing).

### 4.9 Engine resilience — decorators
Cross-cutting reliability wraps any `IOCREngine` without touching it: **Retry** (tenacity, exponential backoff) and **Circuit Breaker** for the network VLM; a **Proxy/cache** (content-hash → result, L1 memory + L2 SQLite) to avoid re-OCRing identical lines; a **timeout** decorator for subprocess engines. Composable via constructor wrapping at the composition root.

### 4.10 Reconcile / vote — pure policy
`IReconciler.reconcile(candidates) -> OCRLine` as a swappable **Strategy** (confidence-weighted vote; Calamari cross-fold voting when enabled). The **grounding guard** is a Policy object: VLM text with no box overlap against a verifiable engine is rejected/flagged, never accepted as sole source (ARCHITECTURE.md §3.6). Pure function of candidate lines → chosen line + review flags.

### 4.11 Post-correction — pure functional, suggest-only
The differentiator (ARCHITECTURE.md §5). A **Chain of Responsibility** of correctors, each `OCRLine -> list[Suggestion]` (never a mutated line): NFC normalize (the one in-place transform, Unicode hygiene only), diacritic validator, ligature/abbreviation expander (reversible), and per-variety lexicon highlighter (Modern Hunspell / Ancient CLTK / Byzantine / **Pontian** custom). Lexicon membership tests use the **Specification** pattern so rules compose (`is_pontian AND not_in_standard_lexicon`). All pure → property-testable (e.g. NFC idempotence, "corrector output length in suggestions, source text byte-identical").

### 4.12 Export — Template Method + Strategy + Builder
`IExporter.export(document) -> bytes` per format (ARCHITECTURE.md §6). A **Template Method** base fixes the skeleton (open → write provenance metadata → serialize → finalize); format **Strategies** fill specifics: searchable PDF **overlaying the original image** (fixes the prototype's blank-canvas bug), DOCX with Byzantine-capable fonts (New Athena Unicode / Gentium Plus / Athena Ruby — fixes the Palatino bug), Markdown/TXT, ALTO/PAGE-XML. A **Builder** assembles the DOCX/PDF document tree from `DocumentStructure`.

### 4.13 Pipeline orchestrator — imperative shell
`application/pipeline.py` composes the filters and threads `Result`. It is the **only** place that sequences side effects; everything it calls is either a pure stage or a port. Optionally a lightweight **Mediator/CQRS** split (`RunOcrCommand` vs read queries) for the server/cloud editions where request/worker separation matters.

### 4.14 Job queue & worker — Command + Repository
An OCR job is a **Command** object; `IJobStore` is a **Repository** for job state + checkpoints; progress is broadcast via an **Observer** event bus (`IEventBus`) that the UI subscribes to. Three adapters: in-process `ThreadPoolExecutor` (Desktop), RQ (Server), Celery/Redis (Cloud) — one port, three wirings.

### 4.15 Review UI — MV* + Observer
Image-line ↔ text pane, confidence heatmap, disagreement highlight, polytonic keyboard (ARCHITECTURE.md §3.8). **MVVM** (Streamlit/Tauri): view-model exposes immutable snapshots; corrections emit events that persist as `Suggestion`s / ground-truth ALTO. No engine or OpenCV calls in the view (ARCHITECTURE.md §9).

### 4.16 Composition roots — Abstract Factory + DI
`composition/{desktop,server,cloud}.py`: hand-wired factory functions that construct adapters and inject them into the pipeline (constructor injection — no DI framework needed; KISS + testability). This is where editions diverge (which engines, which queue, VLM on/off) and the *only* place concrete infrastructure is named.

### 4.17 Config — typed settings
`pydantic-settings` schema, env-backed, validated at startup (fail fast). Secrets (VLM API key) from env/secret manager, never source (security §6). Per-edition profiles.

### 4.18 Observability — decorators + event bus
`structlog` structured logs; per-stage timing and per-page metrics (engine used, mean confidence, est. CER, review-flag count) via a **Decorator** around stages and the **Observer** event bus; `prometheus-client` in server/cloud, no-op sink in desktop. Metrics are also the CER-regression signal in CI.

---

## 5. Cross-cutting: faithfulness enforced structurally

Because faithfulness is the product thesis, it gets mechanical enforcement, not just a coding rule:

- **Immutable lines + separate suggestion layer** (§4.1, §4.3): post-correction *cannot* edit source text — it can only append `Suggestion`s keyed to a line id.
- **Provenance on every line** (§4.3): exports embed which engine/model/confidence produced each token → auditable and reproducible.
- **Grounding guard** (§4.10): unverifiable VLM text is flagged, never silently accepted.
- **Faithfulness test** (§8): a CI test round-trips a page and asserts source text bytes are unchanged by the correction chain (only suggestions differ). A PR that violates suggest-only fails the build.

---

## 6. Concurrency, resilience, security

- **Concurrency** — CPU-bound OCR runs off any request loop via `ThreadPoolExecutor`/`asyncio.to_thread` or the edition's queue (ARCHITECTURE.md §9). Parallelism is **across pages** (embarrassingly parallel); a warm engine/model pool avoids reload cost. Bounded queues give back-pressure.
- **Resilience** — per-page `Result` isolation; resumable checkpoints; retry + circuit breaker on VLM; dead-letter store for pages that fail all engines (surfaced to review, never dropped).
- **Security** — upload validation (magic-byte + size caps), path-traversal guards on export paths, subprocess sandbox + resource limits for Calamari, secrets from env, and for server/cloud: rate limiting, CSRF on state-changing forms, tenant isolation via `TenantContext`, security headers/CSP. `bandit` + `pip-audit` in CI.

---

## 7. Tooling baseline

| Concern | Choice |
|---|---|
| Package/deps | `uv` (fast, lockfile) or `hatch`; `pyproject.toml` per package with extras |
| Format/lint | `black`, `isort`, `ruff` |
| Types | `mypy --strict` |
| Tests | `pytest`, `pytest-cov`, `pytest-xdist`, `hypothesis` (property), `syrupy` (snapshot) |
| Security | `bandit`, `pip-audit`, license check (fail if `calamari_ocr` imported into core) |
| Hooks | `pre-commit` running the above on changed files |
| Docs | Markdown + ADRs in `docs/adr/` |

---

## 8. Test strategy

Pyramid + domain-specific gates:

1. **Unit (fast, pure)** — domain invariants, every post-corrector, reconcile policies, router rules. Property tests: NFC idempotence, "correction never mutates source text," box arithmetic.
2. **Contract tests** — one suite per **port**, run against every adapter (e.g. all `IOCREngine`s satisfy the same behavioral contract). Guarantees adapters are swappable.
3. **Integration** — each engine against a small **fixture corpus** of real printed Greek pages (modern, polytonic, ancient, Byzantine-print, Pontian) with ground-truth text.
4. **Regression gate (the key gate)** — CER/WER computed per fixture; CI **fails if CER regresses** beyond a per-script threshold vs the committed baseline. This is what keeps "improvements" from silently degrading a script variety.
5. **Export snapshots** — DOCX/PDF/ALTO golden-file comparison (structure + provenance), font-coverage assertion for Byzantine glyphs, "searchable PDF text layer overlays the image" assertion.
6. **E2E smoke** — Desktop: ingest a 3-page PDF → export all formats. Server/Cloud: submit job → poll → download, plus resume-after-kill.

Coverage: ≥80% overall, ≥95% domain/post-correction. Fixtures live in `tests/corpus/` with licensing noted.

---

## 9. CI/CD

GitHub Actions, staged so cheap checks fail first:

1. `pre-commit` (format/lint/type) →
2. unit + contract + property (matrix: Windows + Linux, Py 3.11/3.12) →
3. integration + **CER regression gate** (Tesseract + Kraken installed via cached system deps/models) →
4. security (`bandit`, `pip-audit`, **license-isolation check**) →
5. build wheels for `omniocr` + each edition; publish artifacts.

Models pinned by hash in `models/` (git-lfs); CI verifies hashes before integration tests → reproducibility (§1.6).

---

## 10. Phased delivery plan

Maps to ARCHITECTURE.md §8. Each phase ships something usable and ends only when it meets §1 (Definition of production-grade) and its acceptance criteria.

### Phase 0 — Foundation (scaffold + rails)
Stand up `packages/omniocr` with domain models, `Result`, ports, config, logging, the empty pipeline, `pyproject` + extras, pre-commit, CI skeleton, and the test harness with an empty corpus.
**Acceptance:** CI green on an empty pipeline; `mypy --strict` clean; a no-op page flows through the orchestrator returning `Ok`.

### Phase 1 — Printed-Greek core, CPU (the useful MVP)
Real `TesseractEngine` + `KrakenEngine` (Adapters + resilience decorators), PyMuPDF streaming ingest + checkpoint, preprocess chain, script router, reconcile, and faithful post-correction (NFC + diacritic validation + suggest-only lexicon). Wire into the **Desktop** composition root. Retire `ocr.py` at parity.
**Acceptance:** ingest a real multi-hundred-page PDF within bounded memory; Kraken beats Tesseract CER on the polytonic fixture; **faithfulness test passes**; TXT + DOCX (correct fonts) + searchable PDF **over the original image** export correctly; ≥80% coverage; CER baselines committed.

### Phase 2 — Layout & faithful exports
Critical-edition region classification + reading order, searchable-PDF/ALTO/PAGE-XML with provenance, correction UI + polytonic keyboard, ground-truth capture.
**Acceptance:** apparatus/main-text separated on a critical-edition fixture; ALTO round-trips; corrections persist as suggestions/ground-truth without mutating source; export snapshots gated.

### Phase 3 — Variety coverage
Ligature/abbreviation expansion layer (reversible) + Byzantine and **Pontian** lexicons (suggest-only, dialect-scoped so a generic corrector can't "fix" real Pontian).
**Acceptance:** Byzantine-print fixture ligatures expand reversibly; Pontian fixture flags dialect words without rewriting; per-script CER gates hold.

### Phase 4 — VLM opt-in + Calamari booster
Cloud-API `VLMEngine` behind toggle with grounding guard; `CalamariEngine` subprocess adapter as opt-in voting booster; both as optional extras.
**Acceptance:** VLM off by default; grounding guard rejects ungrounded VLM text (tested); Calamari runs isolated (no `calamari_ocr` import in core — license check enforces); base install stays TF-free.

### Phase 5 — Server & Cloud editions
Server (FastAPI + RQ) and Cloud (FastAPI + Celery + Redis, multi-tenant) composition roots over the same core; queue adapters; API hardening; observability dashboards.
**Acceptance:** submit→poll→download E2E; resume-after-kill; tenant isolation; rate limiting + security headers; metrics exported.

### Phase 6 (v2) — Training loop
Kraken/Calamari fine-tune on specific Byzantine printed typefaces via MLflow, fed by Phase 2 ground truth.
**Acceptance:** a fine-tuned model measurably lowers CER on its target typeface vs the pretrained baseline, tracked in MLflow.

---

## 11. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Kraken Greek model accuracy on a given typeface | Poor CER on hard pages | Calamari voting (Ph4) + v2 fine-tune (Ph6); route/override per doc |
| Calamari GPLv3 + TensorFlow contamination | Legal + dependency bloat | Subprocess isolation + optional extra; CI license-import check |
| VLM hallucination on scholarly text | Faithfulness breach | Opt-in only, grounding guard, never sole source (§4.10) |
| Large-PDF memory blowup | Crash on real books | Streaming generators + bounded batches + checkpoints (§4.4) |
| Byzantine glyph coverage in exports | Corrupted output | Font-coverage assertion in export snapshot tests (§8) |
| Model drift across versions | Non-reproducible scholarship | Hash-pinned models + provenance on every line (§1.6, §4.3) |

---

## 12. Immediate next actions (Phase 0 → 1)

1. Create `packages/omniocr` skeleton (domain, ports, application, infrastructure, composition) + `pyproject` with extras.
2. Port the working OpenCV preprocessing and Tesseract path from `ocr.py` into adapters behind the ports.
3. Add `KrakenEngine` adapter + model pinning.
4. Stand up the fixture corpus (a handful of pages per script variety) + CER harness and commit baselines.
5. Wire the Desktop composition root and ship the Phase 1 MVP.
