# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project has not yet reached a tagged release — everything below is
tracked under `[Unreleased]` until `v0.2.0` is cut per
`implementation_plan.md` §5 (Phase 2 exit criteria).

## [Unreleased]

### Fixed — Kraken fine-tuning could not run

`KetosTrainer` called `python -m kraken train` with options Kraken 7 does not have, so the A2
fine-tuning path had never run. It now calls Kraken's `ketos train` (`--format-type path`, a path
list passed with `--training-data`, `--device` on the `ketos` group, early stopping off, learning
rate 1e-4) and copies the best `best_*.mlmodel` to `ketos_finetuned.mlmodel`. `TrainingDataExporter`
writes a `.gt.txt` sidecar beside each line image, which is what `ketos` pairs by name. The `decay`
parameter is gone: `ketos` has no such option. A leave-one-page-out run on the real scans cut
character errors about 18% and word errors about 29% (see `docs/ENGINE_ACCURACY.md`).

### Fixed — server and cloud images, job status, model default

A live run of the cloud stack found the image could not OCR a page (PR #30 fixed its
dependencies). This follows up on what that run left open:

- **Kraken's model default** is resolved in one place. `Settings.kraken_model_path` defaults to
  `""`, which Kraken read as the working directory, so the server, cloud and desktop editions
  could not load a model unless the operator set `OMNIOCR_KRAKEN_MODEL_PATH` by hand. The CLI's
  manifest-default lookup moved to `model_manifest.resolve_kraken_model`, and
  `create_ensemble_pipeline` uses it when the path is empty. The cloud Dockerfile no longer
  hard-codes a model name that duplicated `models/manifest.json`.
- **A job whose pages all failed** is now FAILED. A failed page stays in the document with its
  failure, so such a run produced an empty result and the job ended `done`.
- **The server edition** has a Dockerfile and `docker-compose.yml` (FastAPI, an RQ worker, and
  password-protected Redis), and CI builds the image. Both image jobs now import `cv2`,
  `pytesseract` and `structlog` and resolve the default model, since each was missing once and
  failed only when a job ran.
- **Git LFS models.** The Kraken models are committed as LFS pointers, but no repo rule said
  so: only a machine-wide attributes file made LFS work, so a fresh clone, CI and every image
  built from one held ~130-byte stubs instead of models. `.gitattributes` now tracks
  `models/*.mlmodel`, the image jobs `git lfs pull` the manifest default and verify its pinned
  hash inside the built image, and a pointer file is refused with a message saying to run
  `git lfs pull`.
- **Failed pages are visible in Markdown.** A page that failed was exported as an empty
  `## Page N` section, indistinguishable from a blank page, so a partly failed book looked
  complete. It now carries `> **Page N could not be read:** <reason>`. The server and cloud
  results are Markdown, so they show it too.
- Docs: Calamari described as the opt-in line voter it is (not a 5-model page ensemble),
  CONTRIBUTING lists every gate CI runs, and the install lines name the `tesseract` and
  `desktop` extras.

### Fixed — Calamari wired and tested; dead code removed (audit remediation PR9)

- **Calamari** now works against real Calamari 2.x and is reachable from the CLI
  (`omniocr run --calamari-model CHECKPOINT`). The adapter passed `--files`, which Calamari
  2.x rejects with `UnknownArgumentError`, and parsed a `{"texts": [...]}` stdout that it never
  prints, so it returned an error on every page; its tests only parsed that invented format.
  It now segments lines with the pipeline's own analyzer (Calamari reads lines, not pages),
  runs one `calamari-predict` process per page, and reads the per-line `.json` files.
  Checkpoints resolve through `.json`, because Calamari renames `.h5` files on first load.
  A real-binary test runs when `calamari-predict` and `OMNIOCR_CALAMARI_MODEL` are present.
- **[20]** Deleted `RegistryAwareRouter`, `ScriptRouter`'s unused model-registry path
  (`_PromotedResolver`, `EngineFactory`, `engine_map`, `_build_engine_on_checkpoint`; no
  composition root passed a registry), `refuse_unless_test_split` and `EngineFamily`.
- **[20]** One diacritic check: `model_selection` now counts `post_correction.diacritic_violations`
  instead of keeping a copy. A base character with both a double breathing and a double accent
  now counts as two violations in the model-selection score, not one.
- Docs and docstrings aligned with the code (layout fallback, composition package, cloud/server
  worker claims, test pointer, lexicon comment).

### Fixed — desktop review UI (audit remediation PR8)

- **[9]** Edit mode no longer raises `NameError` (`bar` and `badge` were bound
  only in view mode): the per-line block is split into `_render_line_view` and
  `_render_line_edit`. OCR text is HTML-escaped once, in `line_view_html`, so
  `καὶ <τὸν> λόγον` stays visible; the engine-comparison pane is escaped too.
- Editing a line that already had an accepted suggestion is no longer silently
  lost. Accepted text outranks edited text, but typed edits and glyph inserts
  always wrote to `edited_lines`, so they were shadowed and the text box snapped
  back. `_commit_edit` now writes to the accepted store for an accepted line,
  and typing the OCR text back clears both stores.
- **[S4]** Image uploads no longer go to PyMuPDF as a PDF. `page_images` renders
  PDFs, and reads PNG/JPEG/TIFF with the ingest frame handling, so a multi-page
  TIFF gives one image per page.
- "Undo last action" reverses the true last write (`UndoLog`), even when a key
  was set twice, and works across accepted and edited lines. It also clears the
  text box's remembered text, which used to write the undone edit straight back;
  "Clear all corrections" had the same problem.
- `persist_reviewed_lines` writes its rows through `ICorrectionStore.append_many`,
  one transaction: a failure partway saves nothing, so a retry does not duplicate
  rows.
- The reading-order overlay scales the boxes to the page image (they are in
  300 dpi pixels; the pane image is 150 dpi), and the searchable-PDF button
  reports an image upload or missing Greek font as an error via
  `SearchablePdfExporter.create`.
- New `desktop` extra (`streamlit`); CI installs it and runs the UI through
  Streamlit's `AppTest` (`tests/test_review_ui.py`).

### Fixed — server and cloud editions (audit remediation PR7)

- **[6], [7]** Job state comes from the queue (`IJobQueue`: `RqJobQueue`,
  `CeleryJobQueue`), not an in-process `_jobs` dict, so the APIs no longer need
  a single uvicorn worker. RQ jobs get a 6 h timeout (default was 180 s) and a
  7-day result TTL. The worker writes `<job>.md` atomically; the API serves it
  once the queue says DONE.
- **[8]** The upload is validated once, at submit, against its real filename.
  The worker's hard-coded `"upload.pdf"` re-check is gone, so PNG uploads work.
- **[12]** `job_id` is always server-generated. Every route needs
  `Authorization: Bearer <key>` (`OMNIOCR_API_KEYS`, constant-time compare); a
  job is visible only to its tenant. CORS and trusted hosts default to
  same-origin and `localhost`. Redis is no longer published or open.
- **[S5]** A Redis/broker outage at submit is a 503 and removes the stored upload.
- **[S6]** Handlers are plain `def`; uploads stream to disk with the byte limit
  enforced while copying (413), and the queue carries the path, not the bytes.
- **[S7]** The cloud image installs Tesseract with `ell` and `grc`, installs the
  package non-editable, and runs as a non-root user. CI builds it and checks
  the language packs.
- The Celery broker now follows `OMNIOCR_REDIS_URL` (it was hard-coded to
  localhost, ignoring the compose env), and the unused `max_retries` is gone.

### Fixed — export and CLI (audit remediation PR6)

- **[17]** Searchable PDF text lands inside its box on `/Rotate` 90/180/270
  pages: baselines go through `page.derotation_matrix` and text is rotated with
  the page. Before, it was placed in the unrotated page's space.
- **[18]** `SearchablePdfExporter.create` rejects a non-PDF source and a missing
  or non-Greek font up front. `omniocr run --out x.pdf` on a PNG now exits 3
  before recognizing page 1 instead of failing at export.
- **[S8]** The output file is written to `<name>.part` and swapped in with
  `os.replace`, so a failed write no longer destroys the previous output.

### Fixed — engines and preprocessing (audit remediation PR5)

- **[14]** Despeckle builds one mask from the component areas instead of a
  full-image comparison per component; output is byte-identical.
- **[15]** VLM output is grounded in the pipeline. `GroundedEngine` wraps
  `VLMEngine` and `UnlimitedOCREngine` and drops blocks that Tesseract does not
  corroborate; if Tesseract fails the engine returns `Err`, never ungrounded
  blocks. A VLM reply with no `x1,y1,x2,y2|text` line is now an `Err` instead of
  full-page-box words. `VLMEngine.extract_guarded` and the `grounding_guard`
  constructor argument are removed (nothing called them).
- **[16]** Kraken returns `Err("kraken segmentation found no lines")` when
  segmentation finds nothing, instead of an empty `Ok`.
- `ModelManifest.verify` now runs when the ensemble root builds the Kraken
  engine; a listed model whose SHA-256 differs is refused (`ValueError`, exit 3).
  Models the manifest does not list are allowed.

### Fixed — evaluation and training (audit remediation PR4)
- Evaluation dropped every page where the model produced nothing from the mean,
  so a model that read 1 page of 10 scored a perfect CER of 0. An empty output
  now counts as CER 1.0 (that case scores 0.9).
- `TrainingOrchestrator` evaluated placeholder engines whose `extract()` raised
  `NotImplementedError`, so no candidate could ever be judged. It now takes an
  `engine_factory` (`Callable[[Path], IOCREngine]`, wired to `KrakenEngine` by
  `create_training_pipeline`) and evaluates the candidate and its parent
  (`parent_model.model_name` is its weights path) with the same real engine.
- Without an evaluation corpus training now refuses to start with an `Err`,
  instead of training against a no-op evaluator whose zero-sample report could
  never promote. `scripts/train_kraken.py` goes through `create_training_pipeline`
  and takes a required `--corpus` (and `--mlflow-uri`).
- `SampleSpecification.min_line_height` is enforced (a 3 px line is skipped).
  `min_confidence` is removed: a `Correction` carries no confidence to filter on.

### Fixed — suggestions apply to the right text (audit remediation PR3)
- A token suggestion used to replace the **whole line** when accepted: accepting
  `λέγει,` → `λέγη` on `Ὁ θεὸς λέγει,` left the line as `λέγη`. `Suggestion` now
  carries an optional `start`/`end` span and `apply_suggestion` /
  `apply_suggestions` (`domain/models.py`) are the one place it is applied, so
  several accepted suggestions on one line compose. The review UI uses them for
  single and batch accept. The replacement keeps the punctuation attached to the
  word.
- Findings with nothing to apply are flags (`reversible=False`, empty
  `suggestion_text`): the bare "not in lexicon" flag, dangling combining marks,
  multiple breathing/accent marks, and `unverified_hyphen_join` (it spans two
  lines). Accepting one used to overwrite the line with its own text.
- Review UI button keys were built from `(line_id, reason)`, so two token
  suggestions on one line crashed Streamlit with a duplicate widget id.
- Candidates for capital homoglyphs (`ΚAΙ` with a Latin `A`) can now fire: the
  confusion table was fed a lowercased token and its capital rows never matched.
- Abbreviations match whole tokens, not substrings: `εἴδε` no longer proposes
  `ἴδε` → `ὅρα`.
- `merge_suggestion` returns nothing for a line over `MAX_TOKENS` instead of a
  merge that silently dropped the tail.

### Fixed — circuit breaker (audit remediation PR2)
- `CircuitBreakerEngine` is now an explicit CLOSED / OPEN / HALF_OPEN state
  machine. It used to record the open time *before* the failing call, so a hung
  server whose call lasted `reset_timeout` (Unlimited-OCR: ten minutes) left the
  circuit already expired and every page paid the timeout again. The time is now
  taken after the call, and after the timeout exactly one probe is let through
  while other workers are refused until it settles.

### Fixed — page identity and lost pages (audit remediation PR1)
- Line ids are unique across the document (`p{page}-line-{n}`), set in one place
  (`page_scoped_id`). Engines number lines per page, so a review edit, a
  suggestion, a ground-truth crop or an ALTO `ID` on page 2 used to collide
  with page 1's.
- A page where every engine failed is reported as an `EngineError` failure
  instead of passing as a page of empty lines.
- One unreadable PDF page (or TIFF frame) is reported on that page; the pages
  after it are still read. A multi-page TIFF now yields one page per frame.
- `run()` is a fold over the same lazy path as `run_iteratively()`. With
  `--workers K` at most `2K` pages are in flight (it used to hold the whole
  book), `--max-pages` stops after about `N + 2K` pages, and the sequential
  path catches any per-page error, as the parallel one did.

### Removed
- The unused checkpoint/resume machinery: `IJobStore`, `infrastructure/jobs.py`
  (`InMemoryJobStore`, `SQLiteJobStore`, `RedisJobStore`), and the `job_store`,
  `checkpoint_every`, `job_id` and `resume_job_id` parameters. No caller ever
  passed a job id.

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
