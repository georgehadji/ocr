# ADR 006: ThreadPoolExecutor for Page-Level Parallelism

**Status:** Accepted
**Date:** 2026-08-01
**Deciders:** OmniOCR implementation team

## Context

The OCR pipeline processes pages sequentially. For multi-hundred-page documents,
the wall-clock time is dominated by per-page OCR engine calls (Tesseract, Kraken).
These engines are CPU-bound but release the GIL during C-extension operations,
making them suitable for Python threading.

## Decision

We added an optional `max_workers` parameter to `PipelineOrchestrator`. When set,
the pipeline collects all RawPages from the source, submits each to a
`concurrent.futures.ThreadPoolExecutor`, and reassembles results in page-number
order.

**Rationale:**
1. **Thread safety is simple:** `_process_page()` is stateless — each call operates
   on a single RawPage and returns a DocumentPage with no cross-page dependencies.
2. **GIL release:** Tesseract (via pytesseract/pybind11) and Kraken (via PyTorch
   C++ ops) release the GIL during inference. A ThreadPoolExecutor achieves real
   parallelism without `multiprocessing` complexity.
3. **Safe default:** `max_workers=None` preserves the existing synchronous path,
   making this a zero-risk opt-in.
4. **Ordering:** Results are collected in page-number order before yielding,
   preserving the sequential contract.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| `multiprocessing.Pool` | Serialization overhead for large image bytes; harder to debug; platform-specific spawn/fork issues on Windows |
| `asyncio.to_thread` | Not applicable — the pipeline is synchronous; Celery/RQ already handle async dispatch |
| Per-engine parallelism | Would require significant engine refactoring; page-level is simpler and gives larger speedup |
| ProcessPoolExecutor | Same issues as multiprocessing.Pool + executor shutdown on Windows |

## Consequences

- **Positive:** Wall-clock time for multi-page documents scales with `max_workers`
  (up to I/O and GIL contention limits).
- **Positive:** Per-page failure isolation is preserved — a crashing engine on
  page N does not affect page N+1.
- **Neutral:** `run()` and `run_iteratively()` share one lazy path. With
  parallelism, up to `2 × max_workers` pages are in flight and pages are yielded
  in page order, so progress still streams. (Amended by audit remediation PR1;
  this ADR originally buffered every page before yielding.)
- **Neutral:** Event-bus operations are published from the consuming thread in
  page-number order, avoiding threading issues in those adapters.
- **Negative:** Memory footprint grows by the in-flight window
  (`2 × max_workers` raw pages), not by the document.

## Compliance

- Faithfulness invariant: preserved (pages are independent).
- 80% coverage CI gate: maintained (new tests added).
- ruff/mypy strict: clean.
