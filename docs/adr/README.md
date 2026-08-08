# Architecture Decision Records — OmniOCR

## ADR-001: Hexagonal Architecture

**Status:** Accepted
**Context:** The production plan requires clean separation between business logic and external dependencies.
**Decision:** Domain + Ports + Application + Infrastructure + Composition layers. Dependencies point inward.
**Consequences:** All business logic lives in `packages/omniocr/src/omniocr/`. Edition UIs and controllers compose from infrastructure adapters.

## ADR-002: Faithfulness by Construction

**Status:** Accepted
**Context:** Scholarly OCR requires that recognized source text can never be silently altered.
**Decision:** All domain models are frozen dataclasses with `slots=True`. Post-correction returns `Suggestion` objects keyed to line IDs — source text is never mutated. VLM output is grounding-guarded.
**Consequences:** Every exported line is auditable. CI fails if a PR mutates source text.

## ADR-003: Railway-Oriented Error Model

**Status:** Accepted
**Context:** A single bad page should never kill a batch OCR job.
**Decision:** `Result[T, E]` (Ok/Err) threads through the pipeline. Exceptions are reserved for programmer errors and boundary failures, not control flow. Per-page failures are wrapped in `PageFailure` and retained.
**Consequences:** Callers always handle results explicitly. No hidden exceptions.

## ADR-004: Optional Dependency Extras

**Status:** Accepted
**Context:** The base install must be lean and license-clean. Calamari is GPLv3.
**Decision:** 12 extras (`pdf`, `kraken`, `tesseract`, `vlm`, `calamari`, etc.). Calamari is subprocess-isolated — no `import calamari_ocr` in core. CI enforces this.
**Consequences:** `pip install omniocr` is permissive. GPLv3 liability only applies to users who opt into `omniocr[calamari]`.

## ADR-005: Event Bus for Pipeline Observability

**Status:** Accepted
**Context:** Desktop, Server, and Cloud editions need different progress-reporting mechanisms.
**Decision:** `IEventBus` protocol with synchronous `InMemoryEventBus` implementation. Pipeline publishes `page_completed`/`page_failed` events with timing. Editions subscribe as needed.
**Consequences:** Desktop polls synchronously. Server/Cloud use event-driven updates. No edition-specific code in the pipeline.

## ADR-006: ThreadPoolExecutor for Page-Level Parallelism

**Status:** Accepted
**Context:** Sequential page processing bottlenecks multi-hundred-page documents.
**Decision:** Optional `max_workers` parameter enables `ThreadPoolExecutor`-based page-level parallelism. Tesseract and Kraken release the GIL during inference, achieving real parallelism with threads.
**Consequences:** Wall-clock time scales with worker count. Default `None` preserves synchronous behavior. Results always reassembled in page-number order. See `docs/adr/003-threadpoolexecutor-parallelism.md` for full details.
