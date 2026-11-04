# Audit remediation plan

Fixes for the 20 findings and the 8 smaller ones from the whole-repo audit of
2026-11-04. Each fix keeps the existing architecture: dependencies point inward
(`interfaces → infrastructure → application → domain`), ports are `Protocol`s in
`ports/interfaces.py`, results are `Result[T, E]` values, domain models are frozen
dataclasses, and editions differ only in their composition root.

Numbers in brackets refer to the audit report: **[1]–[20]** are the main
findings, **[S1]–[S8]** the smaller ones:

| | Smaller finding |
|---|---|
| S1 | Alignment merge truncates lines over 512 tokens |
| S2 | Capital Latin homoglyphs never get a Greek proposal |
| S3 | Abbreviations match as raw substrings |
| S4 | Desktop review UI crashes on image uploads |
| S5 | Server ignores `OMNIOCR_REDIS_URL`; orphan job on enqueue failure |
| S6 | Whole uploads pushed through Redis, enqueued on the event loop |
| S7 | Cloud Dockerfile has no Tesseract and installs before copying the source |
| S8 | CLI output write is not atomic |

---

## 0. Principles that decide every fix

1. **Faithfulness first (CLAUDE.md rule 1).** No fix may change recognized text.
   Fixes 2, 3 and 19 turn silent loss into a *reported* failure; they never
   invent text.
2. **Failures are data, not control flow.** A bad page becomes a `DocumentPage`
   with a `PageFailure`, in every path (CLI, server, cloud). No stage may end
   the stream early or return a partial document as `Ok`.
3. **One rule, one place.** Where the audit found the same rule written twice
   (page IDs, sequential vs parallel runs, server vs cloud routes, diacritic
   checks), the plan keeps one copy and deletes the other.
4. **Delete before you build.** Unused machinery (checkpointing, Calamari,
   placeholder routers) is removed, not repaired. It comes back when a caller
   needs it.
5. **Every fix ships with the test that would have caught it.** One test that
   fails on the old code is enough.

## 1. Paradigms by layer

| Layer / module | Paradigm and patterns used in this plan |
|---|---|
| `domain/` | Immutable value objects (frozen, `slots=True`, defaulted new fields so every existing call site compiles). Pure functions for rules (`page_scoped_id`, `apply_suggestion`). |
| `ports/interfaces.py` | Structural typing (`Protocol`). One new port: `IJobQueue`. No abstract base classes. |
| `application/pipeline.py` | Lazy generator pipeline. A bounded producer-consumer window for parallel pages. Errors as values per page (each page is total: it always yields a `DocumentPage`). |
| `application/post_correction.py`, `confusion.py`, `alignment.py` | Pure functions over text plus spans. No I/O. |
| `application/evaluation.py`, `training_orchestrator.py` | Strategy (evaluator) plus Factory (engine built from a model path) injected by the composition root. |
| `infrastructure/resilience.py` | Decorator over `IOCREngine` with an explicit state machine (closed, open, half-open) and an injected clock. |
| `infrastructure/grounding.py` | Decorator: `GroundedEngine` wraps any box-emitting VLM engine. |
| `infrastructure/ingest.py` | Generator with per-item fault isolation. |
| `infrastructure/exporters.py` | Smart constructor (validate at build time, return `Result`). |
| `infrastructure/preprocess.py` | Vectorized NumPy instead of a per-component Python loop. |
| `composition/` | Composition root: the only place that chooses concrete engines, decorators and factories. |
| `editions/server`, `editions/cloud` | Adapter pattern behind `IJobQueue` (RQ and Celery adapters), one shared FastAPI router factory, thin controllers (rule 4), blocking work off the event loop (rule 6). |
| `editions/desktop/review_ui.py` | Separate state from rendering: small pure render helpers, escaped output, an undo stack instead of a dict. |
| `interfaces/cli.py` | Fail fast before the long run; atomic file replace at the end. |

## 2. Delivery order

Nine PRs, each green on every CI gate (ruff, ruff format, mypy strict, layering,
license isolation, pytest with 80% coverage).

```
PR1 page identity + failures-as-data + streaming  ──┬─> PR3 suggestions ──> PR8 desktop UI
                                                    ├─> PR4 eval + training
                                                    └─> PR7 editions HTTP ──> PR9 lean + docs
PR2 circuit breaker          (independent)
PR5 engines + preprocess     (independent)
PR6 export + CLI             (independent)
```

PR2, PR5 and PR6 touch no file that PR1 touches, so they can run in parallel
with it. PR3, PR4, PR7 and PR8 need PR1's unique line IDs or its new `run()`.

---

## PR1 — Page identity, failures as data, streaming

### [1] Line IDs unique across the document

- **Where:** `application/pipeline.py:_process_page` and `_compose_line`;
  `infrastructure/text_layer.py:244,261`; `infrastructure/review.py:122-125`;
  `application/ground_truth.py:98`.
- **Pattern:** one pure function in `domain/models.py`, applied at one boundary:
  ```python
  def page_scoped_id(page_number: int, local_id: str) -> str:
      """Line ids are unique per document: engines number lines per page."""
      return f"p{page_number}-{local_id}"
  ```
- **Change:**
  - In `_process_page`, rename every segment once, right after layout:
    `replace(segment, id=page_scoped_id(raw_page.number, segment.id))`.
    Engine and variant suffixes still come from `_compose_line`.
  - Text-layer lines go through the same function, so `text_layer.py` drops its
    own `p{n}-` prefix (no double prefix).
  - Engines keep local IDs (`line-3`); only the pipeline knows the page.
- **Side effects to handle:**
  - ALTO and PAGE-XML now get document-unique `ID`s, as the schemas require.
  - `review.py`, `review_ui.py` and `ground_truth.py` need no logic change once
    the IDs are unique; the crop file name becomes unique as well.
  - `running_heads.detect` may keep its `(page, id)` keys. Simplifying them is
    optional and out of scope.
- **Tests:**
  - A two-page document where both pages have `line-1`: accepting an edit on
    page 2 changes only page 2 (`tests/test_review.py`).
  - Two crops from two pages produce two files (`tests/test_ground_truth.py`).
  - An ALTO export of two pages has no duplicate `ID`.

### [2] A page where every engine failed is a failure

- **Where:** `application/pipeline.py:628-720`.
- **Change:** count `engines_attempted` and `engines_failed` per page while
  filling `engine_results`. If every attempted engine returned `Err`, add
  `PageFailure(error_type="EngineError", message="every engine failed on this page: …")`.
  Keep the lines (layout boxes), so a reviewer still sees the page geometry.
- **Test:** a pipeline with one stub engine that always returns `Err` yields a
  page with that failure; the CLI then exits `EXIT_FAILURE`.

### [3] One unreadable PDF page no longer ends the book

- **Where:** `infrastructure/ingest.py:66-93`, `application/pipeline.py:442-449`.
- **Pattern:** per-item fault isolation in the generator. A failed page travels
  as a domain value, so the application layer never imports an infrastructure
  type:
  ```python
  # domain/models.py
  @dataclass(frozen=True, slots=True)
  class UnreadablePage:          # satisfies the RawPage protocol
      number: int
      error: str
      content: bytes = b""
      width: int = 1
      height: int = 1
  ```
- **Change:**
  - `_stream_pdf` wraps each page's text-layer and pixmap work in its own
    `try`, and yields `UnreadablePage` on failure.
  - Opening the file stays one `try`: an unopenable file is still `IngestError`.
  - `_process_page` turns an `UnreadablePage` into
    `DocumentPage(failures=(PageFailure("IngestError", error),))`.
- **Test:** a source that raises on page 2 of 3 yields 3 pages, page 2 failed,
  and the CLI exits 1.

### [19] Multi-page TIFF

- **Where:** `infrastructure/ingest.py:60-64, 95-103`.
- **Change:** a non-PDF input goes through `PIL.ImageSequence.Iterator`. Each
  frame is re-encoded as PNG and numbered from 1, inside the same per-page
  `try` as [3]. A single-frame image behaves as today.
- **Test:** a 3-frame TIFF built in the test with Pillow yields 3 pages.

### [13] Bounded memory for parallel runs and `run()`

- **Where:** `application/pipeline.py:215-480`.
- **Pattern:** one lazy path. `run()` becomes a fold over `run_iteratively()`:
  ```python
  def run(self, document: bytes, ctx: TenantContext) -> Result[DocumentStructure, PipelineError]:
      pages = tuple(page for _, page in self.run_iteratively(document, ctx))
      if not pages:
          return Err(PipelineError("no pages were produced from the input"))
      return self.assemble(DocumentStructure(pages=pages), ctx)
  ```
  The parallel branch uses an ordered, bounded window: a `deque` of at most
  `2 * max_workers` futures. Take the next page from the lazy stream only when
  the oldest future is yielded.
- **Deletes:** `_run_sequential` and `_run_parallel` (two copies of the
  per-page error rule, which the audit found disagreeing), plus the checkpoint
  machinery: `_maybe_checkpoint`, `job_id`, `resume_job_id`,
  `checkpoint_every` and the `job_store` parameter. No caller passes a job id.
  `infrastructure/jobs.py` and the job-store wiring in both editions go in the
  same PR, to keep the build green.
- **Also fixes:** `--max-pages N --workers K` now stops after about N + 2K
  pages, and `_iterate_sequentially` catches `Exception` per page, the same as
  the parallel path.
- **Tests:**
  - With `--workers 4`, a 50-page stub source never has more than 8 pages in
    flight (count them in the stub).
  - `--max-pages 2 --workers 2` processes at most 6 pages.

---

## PR2 — Circuit breaker [4]

- **Where:** `infrastructure/resilience.py:60-80`.
- **Pattern:** an explicit state machine inside the existing decorator.
  ```python
  class _State(Enum):
      CLOSED = "closed"
      OPEN = "open"
      HALF_OPEN = "half_open"
  ```
- **Change:**
  - Record `_opened_at = self._clock()` *after* the failing call returns.
  - Moving from OPEN to HALF_OPEN admits exactly one probe. Under `--workers`,
    other threads get `circuit is open` until that probe settles: success means
    CLOSED, failure means OPEN with a fresh `_opened_at`.
- **Test:** the stub engine advances the fake clock by `reset_timeout` inside
  `extract` and fails. The next call must return `circuit is open` without
  calling the engine. A second test checks the single half-open probe with two
  threads.
- **Doc fix:** the `composition/desktop.py` docstring ("one timeout per
  window") becomes true.

---

## PR3 — Suggestions that apply to the right text [5], [S1], [S2], [S3]

### [5] Suggestions carry a span; one function applies them

- **Where:** `domain/models.py:134` (`Suggestion`),
  `application/post_correction.py:178-198`,
  `editions/desktop/review_ui.py:412,702,749`.
- **Pattern:** a value object with an optional span, plus one pure function as
  the single source of truth for applying it:
  ```python
  @dataclass(frozen=True, slots=True)
  class Suggestion:
      line_id: str
      source_text: str
      suggestion_text: str
      reason: str
      reversible: bool = True
      start: int | None = None   # span in the line's text; None = whole line
      end: int | None = None

  def apply_suggestion(text: str, suggestion: Suggestion) -> str:
      if suggestion.start is None or suggestion.end is None:
          return suggestion.suggestion_text
      if text[suggestion.start:suggestion.end] != suggestion.source_text:
          return text            # the line changed under it: do not guess
      return text[:suggestion.start] + suggestion.suggestion_text + text[suggestion.end:]
  ```
- **Change:**
  - `post_correction.py` tokenizes with `re.finditer`, so every token
    suggestion knows its span in `line.text`.
  - A bare "not in lexicon" flag gets `suggestion_text=""` and
    `reversible=False`, matching `grounding.py:59`.
  - `unverified_hyphen_join` spans two lines, so it becomes `reversible=False`
    (a review flag), not a line replacement.
  - The review UI calls `apply_suggestion(current_text, s)`. Several accepted
    suggestions on one line then compose instead of overwriting each other.
- **Tests:**
  - Accepting `λέγει,` → `λέγει` on `Ὁ θεὸς λέγει,` keeps `Ὁ θεὸς `.
  - Two accepted suggestions on one line both apply.
  - A flag-only suggestion cannot be accepted.

### [S1] Alignment merge on long lines

- **Where:** `application/alignment.py:97, 205-210`.
- **Change:** `merge_suggestion` returns `None` when
  `len(chosen.text.split()) > MAX_TOKENS`. A capped merge is not a merge.
- **Test:** a 600-token line where all engines agree emits no suggestion.

### [S2] Capital homoglyphs

- **Where:** `application/post_correction.py:44,178`,
  `application/confusion.py:83-96`.
- **Change:** `confusion.suggest` gets the NFC token before lowercasing, so the
  capital table (`A/Α`, `K/Κ`, …) can fire. The lexicon lookup still lowercases
  the generated candidates.
- **Test:** `ΚAΙ` with a Latin `A` proposes `ΚΑΙ`, which the lexicon accepts as
  `καί`. The test goes through `SuggestOnlyCorrector`, not `suggest` directly.

### [S3] Abbreviations on whole tokens

- **Where:** the abbreviation check in `post_correction.py`.
- **Change:** match abbreviations against whole whitespace tokens (or with
  `(?<!\w)…(?!\w)` lookarounds), never as raw substrings.
- **Test:** `εἴδε` gets no `ἴδε` proposal; a line ending in `-στ` gets no `στ.`
  proposal.

---

## PR4 — Evaluation and training [10], [11]

### [10] Empty outputs count as errors

- **Where:** `application/evaluation.py:94-97`.
- **Change:** delete both `if hyp` filters.
  `character_error_rate(ref, "")` already returns 1.0.
- **Test:** a model that reads 1 of 10 lines perfectly and nothing on the
  other 9 scores CER 0.9, not 0.0.

### [11] Training compares real engines

- **Where:** `application/training_orchestrator.py:140-152, 210-235`,
  `composition/training.py:49-63, 107-121`, `scripts/train_kraken.py`.
- **Pattern:** Factory injected by the composition root. The orchestrator knows
  only the port:
  ```python
  EngineFactory = Callable[[Path], IOCREngine]
  ```
- **Change:**
  - `TrainingOrchestrator(..., engine_factory: EngineFactory)` builds the
    candidate engine from the checkpoint path and the parent engine from the
    manifest default path.
  - `composition/training.py` passes
    `lambda path: KrakenEngine(model_path=path, ...)`.
  - Delete `_CandidateEngine` and `_ParentEngine`.
  - Delete `_NoOpEvaluator`: no corpus means training refuses to start
    (`Err`), since an evaluation with 0 samples can never promote anyway.
- **Also:** `SampleSpecification.min_line_height` and `min_confidence` are
  enforced in `ground_truth.py` (a 3 px crop is rejected), matching its
  docstring.
- **Tests:**
  - The orchestrator runs end to end with a stub factory and the real
    `_CorpusEvaluator` over `tests/corpus/`.
  - A crop under the minimum height is skipped.

---

## PR5 — Engines and preprocessing [14], [15], [16]

### [14] Vectorized despeckle

- **Where:** `infrastructure/preprocess.py:271-276`.
- **Change:**
  ```python
  small = stats[:, cv2.CC_STAT_AREA] < self._min_area
  small[0] = False                      # label 0 is the background
  cleaned = image.copy()
  cleaned[small[labels]] = 255
  ```
- **Test:** on a synthetic image with 2,000 specks, the output is
  byte-identical to the old loop, which the test keeps as a reference.

### [15] VLM output is grounded

- **Where:** `infrastructure/vlm.py:118-134, 314-326`,
  `infrastructure/grounding.py`, `composition/desktop.py`.
- **Pattern:** Decorator. A `GroundedEngine(IOCREngine)` wraps any
  box-emitting engine and runs the existing `GroundingGuard` on its blocks.
  The composition root applies it to `VLMEngine` and `UnlimitedOCREngine`.
- **Change:**
  - Delete the unstructured fallback in `vlm.py`. A response without the
    `x1,y1,x2,y2|text` format is `Err`, as in `unlimited_ocr.py`.
  - Delete `extract_guarded`, which the decorator replaces.
- **Test:** a VLM stub that returns plain text yields `Err`. A stub returning a
  box over an empty region is flagged by the guard in the pipeline, not only in
  unit tests.

### [16] Kraken no longer fails silently on segmentation

- **Where:** `infrastructure/kraken.py:196-204`.
- **Change (this PR):** return `Err(EngineError("kraken segmentation found no lines"))`
  when `pageseg.segment` yields 0 lines. The pipeline then logs
  `engine_failed`, and [2] reports the page if Tesseract also failed.
- **Follow-up (separate ADR, not in this plan):** let Kraken recognize the
  layout stage's segments instead of segmenting again. That changes the
  `IOCREngine` contract (an optional `segments` argument), so it needs its own
  decision record and benchmark (`docs/ENGINE_ACCURACY.md`).
- **Test:** a page where segmentation returns no lines gives `Err`.

---

## PR6 — Export and CLI [17], [18], [S8]

### [17] Searchable PDF on rotated pages

- **Where:** `infrastructure/exporters.py:273-306`.
- **Change:** map each insertion point through `page.derotation_matrix`, and
  pass `rotate=page.rotation` to `insert_text`.
- **Test:** export onto a `/Rotate 90` page, then
  `page.get_text("words")` finds the word inside the expected visible box.

### [18] Fail fast before a long run

- **Where:** `infrastructure/exporters.py` (`SearchablePdfExporter`),
  `interfaces/cli.py:_build_exporter`.
- **Pattern:** smart constructor.
  ```python
  @classmethod
  def create(cls, source_pdf: bytes, font_path: Path | None = None
             ) -> Result["SearchablePdfExporter", ExportError]:
      # rejects a non-PDF source and a missing Greek-capable font
  ```
- **Change:** `_build_exporter` returns that `Err`, and the CLI exits
  `EXIT_ENVIRONMENT` before recognizing page 1.
- **Test:** `--out x.pdf` with a PNG input exits 3 without calling the
  pipeline (stubbed `_build_pipeline` never called).

### [S8] Atomic output write

- **Where:** `interfaces/cli.py:646`.
- **Change:**
  ```python
  partial = out.with_name(out.name + ".part")
  partial.write_bytes(exported.value)
  os.replace(partial, out)
  ```
- **Test:** a write that raises leaves the previous output intact.

---

## PR7 — Server and cloud editions [6], [7], [8], [12], [S5], [S6], [S7]

The audit found both editions duplicating routes, keeping state per process,
and trusting the client. These findings are one design problem: there is no
queue port. The fix is the hexagonal one that `CLAUDE.md` already describes:
editions differ only in their composition root.

### New port

```python
# ports/interfaces.py
class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

class IJobQueue(Protocol):
    def submit(self, job_id: str, upload: Path, filename: str, tenant: str) -> None: ...
    def state(self, job_id: str) -> JobState: ...
```

### Adapters

- `editions/server/queue.py`: `RqJobQueue`, built with
  `Redis.from_url(settings.redis_url)` ([S5]). Jobs are enqueued with
  `job_timeout=settings.job_timeout_s` (default 6 h) and
  `result_ttl=settings.result_ttl_s` (default 7 days) ([7]).
- `editions/cloud/queue.py`: `CeleryJobQueue`. The task gets `time_limit`
  below the broker `visibility_timeout`, so `acks_late` never redelivers a
  running book. Delete the unused `max_retries`.
- Status comes from the queue's own backend, never from an in-process dict.
  Deleting `_jobs` removes the single-uvicorn-worker limit.

### Shared HTTP layer

- `editions/common/api.py`:
  `build_router(queue: IJobQueue, settings: Settings) -> APIRouter`, used by
  both `main.py` files. This deletes the duplicated submit, status and result
  routes.
- First check that `scripts/check_layering.py` allows `editions/common`. If it
  does not, extend the rule there in the same PR.

### Controllers (rules 4 and 6)

- Handlers are plain `def`, so FastAPI runs them in its threadpool and the
  event loop is never blocked ([S6]).
- The upload is streamed to `settings.upload_dir / f"{job_id}{suffix}"` with a
  byte limit enforced while copying ([S6]). It is validated once, with the real
  `file.filename` ([8]).
- The queue carries the path, not the bytes. The worker re-check with the
  hard-coded `"upload.pdf"` is deleted ([8]).

### Worker

- One shared function, `run_job(upload: Path, out: Path, pipeline, exporter)`:
  `pipeline.run()` (now streaming, see PR1), export, then an atomic write to
  `settings.result_dir / f"{job_id}.md"` ([6]). The upload is deleted after the
  run.
- `job_result` reads that file only when `queue.state(job_id) is DONE`.

### Security ([12])

- `job_id` is always `uuid4().hex`. Delete the `job_id` form field, which
  removes the overwrite and the `../` path cases.
- Tenant comes from an API-key dependency: `OMNIOCR_API_KEYS` maps key to
  tenant, compared with `hmac.compare_digest`. A result is served only to its
  own tenant.
- CORS origins and trusted hosts come from settings and default to
  same-origin and `localhost`, not `*`.
- `docker-compose.yml`: delete the Redis `ports:` mapping, set `requirepass`,
  and add one named volume shared by the API and worker for uploads and
  results.
- `shortcut:` a shared volume assumes the API and worker run on one host. Move
  to object storage when they are deployed on separate machines. Put this
  comment next to `upload_dir` in the settings.

### Image ([S7])

- `Dockerfile`:
  `apt-get install tesseract-ocr tesseract-ocr-ell tesseract-ocr-grc`, then
  `COPY . .` before `pip install ".[cloud,pdf,kraken]"` (not editable), and run
  as a non-root user.

### Tests

- One API test module runs against both adapters through an in-memory
  `IJobQueue` fake: submit a PNG → status → result ([8]).
- Two tenants with the same filename get separate results.
- A missing API key returns 401.
- An oversized upload returns 413 without the whole body held in memory.
- A Redis connection error at submit returns 503 and leaves no orphan job
  ([S5]).
- A `docker build` smoke job in CI checks `tesseract --list-langs` for
  `ell` and `grc` ([S7]).

---

## PR8 — Desktop review UI [9], [S4]

- **Where:** `editions/desktop/review_ui.py:307-316, 452, 684-727`.
- **Pattern:** render helpers that take state and return nothing else; all
  user text is escaped once at the boundary.
- **Change:**
  - Split the per-line block into `_render_line_view(line)` and
    `_render_line_edit(line)`. `badge` and `bar` exist only in the view helper,
    which removes the `NameError` in edit mode ([9]).
  - `st.markdown(..., unsafe_allow_html=True)` receives `html.escape(display)`,
    so `καὶ <τὸν> λόγον` stays visible ([9]).
  - Page images come from PyMuPDF only when `pdf_bytes.startswith(b"%PDF-")`.
    Image uploads use their own frames, through the same TIFF handling as
    PR1's [19] ([S4]).
  - Undo becomes a list of `(store, key, previous_value)` entries. "Undo last"
    pops the true last action, even when a key was set twice.
  - `persist_reviewed_lines` commits all rows in one transaction, so a failure
    partway saves nothing and a retry does not duplicate rows.
- **Tests:** these go in `tests/test_review.py` through the pure helpers, since
  Streamlit itself is not run in CI.
  - The escaped HTML contains `&lt;τὸν&gt;`.
  - Edit mode renders without error.
  - A PNG upload builds a review document.
  - Undo after setting the same key twice restores the middle value.

---

## PR9 — Delete dead code and align the docs [20]

### Delete

- `infrastructure/calamari.py`, `tests/test_calamari.py`, the Calamari wiring
  in `composition/desktop.py:233-237, 250-251`, `Settings.enable_calamari`,
  the `calamari` extra in `pyproject.toml`, and the Calamari part of
  `scripts/check_license_isolation.py`. Keep the script itself if other GPL
  checks remain; otherwise delete it from CI too.
- `RegistryAwareRouter` (`application/router.py:93-120`),
  `refuse_unless_test_split` (`application/promotion.py:127`) and
  `EngineFamily` (`ports/interfaces.py:99`).
- `ScriptRouter.model_registry`, `_PromotedResolver` and
  `_build_engine_on_checkpoint` stay only if PR4 wires the registry into a
  composition root. If not, delete them as well.
- Merge `model_selection._diacritic_violation_rate` into
  `SuggestOnlyCorrector._check_diacritics`: keep one function and import it
  from the other module.

Before each deletion, grep the whole tree (src, tests, editions, scripts,
docs) for the name, including string references.

### Align the docs (CLAUDE.md rule 8)

| Doc | Change |
|---|---|
| `CLAUDE.md` | Drop Calamari from the engine bank. Say VLM output is grounded by `GroundedEngine`. Drop "single uvicorn worker". |
| `docs/ARCHITECTURE.md` §4 | Same engine-bank and grounding update. |
| `composition/__init__.py`, `composition/desktop.py` | Docstrings match the decorator stack actually built. |
| `infrastructure/tesseract.py` (`TesseractLayoutAnalyzer`) | Describe the [16] behavior: Kraken reports a page with no lines instead of returning nothing. |
| `editions/server/composition.py`, `editions/cloud/tasks.py`, `editions/cloud/main.py` | Describe the API-key, queue and result-file flow from PR7. |
| `tests/test_server.py:5` | Remove the pointer to the missing `test_server_api.py`. |
| `application/ground_truth.py`, `router.py`, `training_orchestrator.py`, `pipeline.py` | Docstrings match PR1 and PR4 behavior. |
| `scripts/build_lexicon.py:178-192` | Say only the accent-stripped form is stored. |

---

## Also noticed (optional, same PRs)

These came up during the audit and fit into the PRs above at little cost:

| Item | PR |
|---|---|
| `ModelManifest.verify` is never called at runtime; call it once when the Kraken engine is built, and refuse a hash mismatch. | PR5 |
| `RetryingEngine` retries immediately; add a short exponential backoff with an injected `sleep`. | PR2 |
| `check_layering.py` misses `from omniocr.infrastructure import X` in editions; match that form too. | PR7 |

## Verification for the whole plan

1. Every PR: the CI gates in `CONTRIBUTING.md`.
2. After PR1 and PR6, run on the real book:
   `omniocr run "Texts for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf" --out book.docx --drop-running-heads --workers 2`.
   Then check that:
   - blank pages are reported, not shown as ok;
   - the page count matches the PDF;
   - memory stays flat (watch the process in Task Manager);
   - the DOCX opens.
3. After PR7: `docker compose up`, submit a PNG and a PDF with an API key,
   then fetch both results with the key and fail to fetch them without it.
