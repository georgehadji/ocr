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
- Device selection is now project-wide in `infrastructure/device.py`, shared by
  the recognizer, the fine-tuner, and `omniocr doctor`. It picks CUDA, then
  MPS, then CPU, and degrades rather than failing if a GPU can't take the model
  or a page. `KetosTrainer` previously hardcoded `device="cpu"` — fine-tuning,
  the most GPU-sensitive step in the project, ran on CPU even on a GPU machine
  and nothing said why. The CUDA answer is `cuda:0`, never a bare `cuda`,
  because `kraken.ketos.util.to_ptl_device` splits on `:` and indexes `[1]`
  unconditionally.
- `omniocr doctor` reports the device at top level and explains a CPU answer
  when the cause is fixable — "no GPU in this machine" and "a GPU the installed
  torch wheel cannot address" are very different problems and only one is a
  one-command fix.
- `scripts/check_layering.py`, a CI gate enforcing the dependency rule
  mechanically: domain purity, inward dependencies, and no edition importing
  an adapter directly.
- Review UI can now commit reviewed lines to the corrections store, so the
  training pipeline finally has a data source. `review_line_to_correction` and
  `SqliteCorrectionStore` both already existed but nothing called them
  together — accepted corrections lived only in Streamlit session state and a
  JSON session file, and `TrainingOrchestrator` read an empty store. The logic
  lives in a testable `persist_reviewed_lines()`, not in the UI.
- `VLMEngine(max_edge_px=...)` downscales pages before sending, cutting image
  tokens ~67% (a 300 DPI A5 page goes from ~3,096 to ~1,032 tokens). See
  `docs/VLM_COST_OPTIMIZATION.md`.
- LICENSE (MIT), this CHANGELOG, CONTRIBUTING.md, SECURITY.md.

### Fixed — defects found by widening the CI gate to `editions/` and `scripts/`
CI linted `packages tests` and typed only `packages/omniocr/src`. The
composition roots and entry points users actually run were outside every gate.
Widening it surfaced 19 lint findings and 71 typing errors, including:
- **Cloud `POST /ocr/submit` failed whenever Celery was importable.** The app
  sets `task_serializer="json"` and the route enqueued raw `bytes` plus a live
  `Settings` dataclass — neither JSON-encodable — so the enqueue raised before
  the worker saw a job. The payload is now base64; the never-read
  `settings_json` parameter is gone from both editions.
- **A failed export was reported as `"completed"`.** Cloud substituted `b""`,
  wrote an empty `.md`, and returned success; the caller downloaded an empty
  file with no signal that anything had gone wrong.
- **`/ocr/status` returned an unrelated job's state**, picking
  `next(j["task_id"] for j in _jobs.values())` — an arbitrary other job.
- **The server's async path could never serve a result.** The worker returned
  bytes into RQ's result backend, nothing moved them to disk, and
  `/ocr/result` read `results/{job_id}.md`, so every queued job 404'd.
- **The review UI shared one module global for two page types.** Streamlit runs
  the script top to bottom, so `page` was both the pipeline's `DocumentPage`
  and the review pane's `ReviewPage` (`OCRLine.id` vs `ReviewLine.line_id`),
  and it was bound only inside a range check that everything below ignored.

### Fixed — the `--json` contract, which the CLI documented but did not honor
- Log lines went to **stdout**, ahead of the payload: nothing called
  `configure_logging`, so structlog fell back to its default `PrintLogger`.
  `omniocr run --json | jq` failed on the first `page_completed` line.
- The payload was **not UTF-8**. `print()` encodes through the console
  codepage — cp1253 on a Greek Windows install, this project's target
  platform — so `json.loads(stdout.decode("utf-8"))` raised
  `invalid continuation byte`. It is now written to `sys.stdout.buffer`.
- Fixing the above exposed the opposite defect: `configure_logging` routed
  structlog through stdlib logging but **never installed a handler**, so every
  edition that called it had been logging into a void.

### Fixed — lexicon lookups missed on essentially every token
Tokens were compared byte-for-byte against NFC word lists, so a decomposed
page flagged *every word on it* as "not in lexicon", and `θεοτόκος,` missed on
its comma. `SetLexicon` now normalizes both sides; only the lookup is
normalized, so suggestions still report the original token verbatim.

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
- `review_ui.py` still has no view-model split (~600 lines). The corrections
  wiring above deliberately put its logic in `infrastructure/review.py` so it
  is testable, but the UI file itself is unrefactored — see E-5 in
  `implementation_plan.md`.
- Whether `max_edge_px=1400` is the right default is unmeasured. It was chosen
  for legibility headroom; calibrate against the grounded/ungrounded ratio from
  `extract_guarded` once an API key is available.
