# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project has not yet reached a tagged release — everything below is
tracked under `[Unreleased]` until `v0.2.0` is cut per
`implementation_plan.md` §5 (Phase 2 exit criteria).

## [Unreleased]

### Added
- Headless CLI (`omniocr run`, `omniocr doctor`) with a stable exit-code
  contract (0 ok / 1 pipeline failure / 2 usage / 3 environment) and a
  `--json` mode for scripted callers.
- `omniocr doctor` reports engine, language-pack, model, and — for Kraken —
  device availability, so a missing `grc` pack surfaces as a clear message
  instead of a silent test skip.
- Kraken now selects a CUDA GPU automatically when one is usable and falls
  back to CPU otherwise, degrading rather than failing if the GPU can't take
  the model or a page.
- `scripts/check_layering.py`, a CI gate enforcing the dependency rule
  mechanically: domain purity, inward dependencies, and no edition importing
  an adapter directly.
- LICENSE (MIT), this CHANGELOG, CONTRIBUTING.md, SECURITY.md.

### Changed
- **Kraken recognition, previously completely broken, now works.** It fed a
  grayscale page to a segmenter requiring a bi-level image and raised
  `Image is not bi-level` on every call — the documented "accuracy driver"
  had never produced a character. Both Kraken entry points now share one
  binarization helper.
- All three editions (desktop, server, cloud) now compose the pipeline
  through `omniocr.composition` instead of hand-wiring adapters. This closed
  four bugs that only existed because of the hand-wiring: the desktop UI ran
  with no recognition engine in its default path; with VLM enabled it used
  the VLM unaudited with nothing box-grounded to reconcile against; both
  server and cloud constructed Kraken with an empty model path; the server
  passed its app name as the Tesseract language.
- `PromotedModel` now carries the checkpoint it was promoted for, and the
  router resolves it through an `engine_factory`. Previously the router
  resolved only the engine *family*, so a promoted fine-tune silently routed
  to the parent, un-fine-tuned weights — every training run was a no-op
  downstream.
- `Result` is a union alias (`Ok[T,E] | Err[T,E]`) instead of a base class,
  so `isinstance` narrowing actually works. This was the root cause of six
  `hasattr(x, "value")` workarounds scattered across the training
  orchestrator, one of which could silently substitute a zeroed evaluation
  report and promote a model against fabricated metrics.
- Pipeline checkpointing batches (default every 25 pages) instead of
  re-serializing the entire accumulated document after every page — the
  previous behavior was O(n²) I/O, worst exactly on the multi-hundred-page
  books this project targets.
- `run_iteratively()` now actually streams. It previously materialized the
  full page list before processing any of them, defeating the point of a
  page-streaming ingest on a large PDF.
- `mypy --strict` runs without `--follow-imports=skip`, so cross-module type
  agreement is actually checked, not just each module in isolation.
- `pyproject.toml` now declares `license = "MIT"` (PEP 639 SPDX expression)
  with `license-files = ["LICENSE"]`.

### Fixed
- All CI gates green: `ruff check`, `ruff format --check`, `mypy --strict`,
  `bandit`, license isolation, and the new layering gate.
- Five bandit findings were false positives (argv-list subprocess, an XML
  *writer*, not a parser) and are annotated with the reason; the one real
  finding — an unvalidated `api_url` scheme reaching `urllib.request.urlopen`
  — is fixed by validating the scheme at construction.
- Generated artifacts (`.coverage`, `.reasonix/`, `*.egg-info/`) were tracked
  despite being gitignored, so every test run dirtied the working tree.
  Untracked (files remain on disk and regenerate).

### Removed
- `editions/legacy-{desktop,server,cloud}` — unreferenced trees predating
  the current composition roots.
- Root `omniocr/__init__.py`, a `pkgutil.extend_path` bridge into
  `packages/omniocr/src/omniocr`. Redundant with the package's own
  `package-dir` configuration once installed, and a real import-shadowing
  risk alongside it.
- `create_desktop_pipeline()` — zero callers, and it had the same
  no-router defect that was just fixed in the desktop UI itself. An unused
  factory with the most inviting name in the package was a trap, not an API.

### Known gaps (tracked, not yet closed)
- The accuracy regression corpus is still substantially synthetic (PIL
  renders of Arial), so Kraken-vs-Tesseract CER claims are not yet
  evidence-backed on real printed Greek. See `implementation_plan.md` F-2.
- The 339 MB target document has never been processed end to end; streaming,
  memory, and resume behavior are validated by unit tests but not by a real
  run. See F-3.
- No metrics export (`prometheus-client`); the `IEventBus` seam exists but is
  unwired. See E-3.
- `prototype/ocr.py` (the original Streamlit prototype) is retained pending
  a deliberate parity check against the current CLI/ensemble pipeline — not
  yet confirmed superseded on every dimension the plan requires.
