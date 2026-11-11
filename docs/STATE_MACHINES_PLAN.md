# Implicit State Machines — Discovery and Remediation Plan

**Date:** 2026-11-11
**Status:** Proposed — nothing implemented yet
**Branch at survey:** `feat/page-workers` (`f36040f`)
**Scope:** `packages/omniocr`, `editions/*`, `scripts/`, `prototype/` (reference only)

---

## 0. Goal and the one decision

**Goal.** Find every place where a lifecycle is encoded implicitly (bare strings,
co-varying booleans/Optionals, key presence, file existence, `Ok(None)` doing
triple duty) and make each one *explicit, guarded, observable* — so that:

- **UX** — users always see *what state* their job / review / model is in, *why* it
  failed, and never lose work silently.
- **DX** — a developer reads one enum + one transition table instead of
  reconstructing a lifecycle from `if` chains across four files; typos fail
  `mypy --strict` instead of silently skipping a branch.
- **AX** — an agent can enumerate every state machine, its states and legal
  transitions from one generated document and from `--json` / HTTP payloads,
  without reading source.

**The decision (one pattern, applied everywhere):**

> Each lifecycle gets a **`StrEnum` of states + a module-level transition table
> `Mapping[State, frozenset[State]]` + one pure guard** in the layer that owns
> the entity. No new dependency, no state-machine library, no runtime framework.
> Values that are already persisted or part of a public contract **keep their
> exact string values** — `StrEnum` members compare equal to, and serialise
> identically to, the old literals, so the change is invisible on the wire and
> on disk.

Why this and not the alternatives:

| Option | Verdict |
|---|---|
| `transitions` / `python-statemachine` library | Rejected: new dependency in the core, runtime magic that `mypy --strict` cannot see, violates "never add a dependency for a few lines". |
| Classes-per-state (State pattern) | Rejected: heavy for 4–6-state enums; fights the frozen-dataclass domain style. |
| Type-state (distinct types per state) | **Kept where it already exists** (`ModelCandidate → PromotedModel`, `domain/training.py:1-7`); extended only to the training outcome (§4.5). |
| **`StrEnum` + transition table + guard** | **Chosen.** Matches the existing `class X(str, Enum)` convention (`domain/models.py:40,50,75,251`, `ports/interfaces.py:160`, `infrastructure/resilience.py:38`), is checkable by mypy (`assert_never` exhaustiveness), is data an agent or doc generator can read, and is backward-compatible on every wire format. |

**Why `StrEnum`, not the existing `(str, Enum)` idiom, for new enums.** The repo
requires Python ≥ 3.11 (`pyproject.toml:9`). Since 3.11, `format()` / f-strings on a
`(str, Enum)` member yield `ClassName.MEMBER`, not the value; `StrEnum` yields the
value. Converting a bare-string field to a `(str, Enum)` would therefore silently
change every f-string, log line and error message that interpolates it.
`StrEnum` does not. Existing `(str, Enum)` classes stay as they are (no churn);
only new enums use `StrEnum`.

---

## 1. Inventory — every implicit state machine found

Survey method: three Sonnet subagents (editions / core / infrastructure+CLI) plus one
Haiku grep sweep; every high-impact claim below was then re-read in source by the
main session. Line numbers are as of `f36040f`; re-check before editing.

Legend — **Kind:** `S` bare strings, `B` co-varying booleans/Optionals, `K` key/row
presence, `F` file existence, `R` return-shape (`Ok(None)` etc.), `E` explicit enum
without transition table. **Risk:** 🔴 user-visible bug or data-quality loss today,
🟠 latent/illegal state reachable, 🟡 clarity/observability only.

| # | Entity | Where | Kind | States today | Risk |
|---|---|---|---|---|---|
| M1 | **OCR job** (server + cloud) | `ports/interfaces.py:160`, `editions/common/api.py:95-150`, `editions/common/worker.py:40-75`, `editions/server/queue.py:21-30,78`, `editions/cloud/queue.py:22-30,55` | E+F+S | `queued/running/done/failed/not_found` + result-file existence + worker `outcome="failed"/"done"` | 🔴 |
| M2 | **Review session** (desktop) | `editions/desktop/review_ui.py:176-262,370-433,530-565` | K+B+F | empty → OCR running → reviewing → (new session); per-line accepted/edited/raw by dict membership | 🔴 |
| M3 | **Correction lifecycle** | `domain/corrections.py:20-75`, `infrastructure/corrections_store.py:125-138`, review UI commit `:544-564` | B+K | `accepted: bool = True`; "revision" = later `corrected_at` | 🔴 |
| M4 | **Training run outcome** | `application/training_orchestrator.py:77-224`, sole caller `scripts/train_kraken.py:168` | R+S | `Ok(None)` ×3 meanings, `Err`, `Ok(PromotedModel)`; event `"model_promoted"` only on success | 🟠 |
| M5 | **Promotion decision** | `application/promotion.py:55-110` | S | `evaluated_on != "test"` literal; `PromotionRefused(message)` untyped | 🟠 |
| M6 | **Page through pipeline** | `application/pipeline.py:~262-614`, `domain/models.py:189,216-221` | S+R | `PageFailure.error_type: str` (`IngestError`/`EmptyPage`/`EngineError`/`type(exc).__name__`); failing *stage* lost | 🟠 |
| M7 | **Pipeline event types** | `domain/models.py:220`, `application/pipeline.py:339-347`, `infrastructure/prometheus_metrics.py:146-154`, `training_orchestrator.py:210-217` | S | `"page_completed"`, `"page_failed"`, `"model_promoted"` — string-matched across modules; a typo silently drops metrics | 🟠 |
| M8 | **Circuit breaker** | `infrastructure/resilience.py:38-110` | E | `closed/open/half_open`; transitions scattered, unobservable | 🟡 |
| M9 | **Kraken device latch** | `infrastructure/kraken.py:165-276` | B+S | `_device` None/`cuda:0`/`mps`/`cpu`, one-way GPU→CPU fallback, `_model_hash="unavailable"` sentinel | 🟡 |
| M10 | **Model registry / promotion record** | `infrastructure/model_registry.py:56-91`, `model_manifest.py:77,143-209`, `mlflow_registry.py:140-216` | K+F | absent → promoted; hash mismatch = silent "quarantined"; corrupt index reads as "nothing promoted" | 🟠 |
| M11 | **Line hyphenation join** | `domain/models.py:261-268`, `application/structure/hyphenation.py:53-93`, `structure/assembler.py:155-243` | S | `verdict` ∈ `joined_in_lexicon/hyphen_in_lexicon/unverified`, `separator` ∈ `""/"-"/" "` (`" "` never produced) | 🟠 |
| M12 | **Suggestion kind** | `domain/models.py:135-~180`, `application/post_correction.py:157-263` | B+S | apply / flag / whole-line derived from `reversible` + `start is None`; `reason: str` open set | 🟡 |
| M13 | **KetosTrainer run** | `infrastructure/ketos_trainer.py:52-189` | F | state = files in work dir; stale `best_*` from a prior run can be picked | 🟠 |
| M14 | **CLI run verdict / exit codes** | `interfaces/cli.py:8,52-58,624,664-691` | S | `EXIT_*` ints; per-page `"ok"/"failed"`; `_EMPTY_PAGE="EmptyPage"` string-matched against M6 | 🟡 |
| M15 | **Engine-set configuration** | `infrastructure/config.py` | B | `enable_vlm` × `vlm_api_key` × `unlimited_ocr_url` × `enable_calamari`; illegal combos accepted | 🟡 |
| M16 | **Undo log** | `infrastructure/review.py:269-322` | K | `previous is None` = "key absent" | 🟡 |
| M17 | **Layout fallback** | `application/layout.py:30-45` | R | primary ok / fallback ok / both empty → `Ok(())` → later `EmptyPage` | 🟡 |
| M18 | **Ingest page kind** | `infrastructure/ingest.py:364-423` | S+R | `%PDF` magic bytes; `ImagePage | UnreadablePage` + raised `IngestError` (two failure channels) | 🟡 |

Already explicit and fine (no action beyond documenting): `Script`, `AgreementTier`
(derived), `RegionType`, `ParagraphRole`, `SplitName`.

### 1.1 Concrete defects found while mapping (verified in source)

These are the reason M1–M3 are 🔴. Each is fixed inside its machine's phase, test-first.

1. **Cloud jobs never report "not found" and unknown states read as running.**
   `editions/cloud/queue.py:55` falls back to `JobState.RUNNING`; RQ
   (`editions/server/queue.py:78`) falls back to `FAILED`. An expired cloud job id
   reads `queued` forever (documented at `cloud/queue.py:18-21`, but the client is
   never told). Two editions, two answers for the same condition.
2. **`done` with no result file is reachable** (result dir not shared, file removed,
   queue TTL outlives file) → client sees `done`, then 404 on `/ocr/result`
   (`api.py:145-149`). No retention ties the two carriers together.
3. **`failed` carries no reason; partial success is indistinguishable from full
   success** (`worker.py:57-61` keeps going when some pages fail; API returns only
   `status`).
4. **Session restore can crash:** `review_ui.py:381-384` reads
   `session.get("ground_truth_lines", {})` then `session['ground_truth_lines']`
   → `KeyError` on an older/hand-edited session file. `current_page` is saved
   (`:186`) but never restored.
5. **Dead "saved" flag:** `("saved", False)` (`review_ui.py:215`) is never read or
   written; there is no unsaved-changes indicator and "New session" discards work
   without confirmation.
6. **Training data duplication:** `all_accepted()` (`corrections_store.py:125-138`)
   returns *every* accepted row, not the latest per `line_id`, contradicting the
   domain contract "a revised transcription is a new `Correction` with a later
   `corrected_at`" (`domain/corrections.py:24-25`). Re-clicking "Commit for
   training" re-appends every row, so the same lines train multiple times, and a
   later `accepted=0` does not retract an earlier acceptance.
7. **Promotion split check is a string literal** (`promotion.py:59`,
   `!= "test"`) while `SplitName.TEST` exists; a registry round-trip reloads
   `evaluated_on` as a free `str` (`model_registry.py:113`).
8. **Metrics silently depend on event-name spelling**
   (`prometheus_metrics.py:146,154`): a renamed `event_type` drops page metrics with
   no error.

---

## 2. Non-negotiables ("without breaking anything")

1. **Frozen contracts — never rename these values or keys:**
   - HTTP: `status` ∈ `queued|running|done|failed`, status codes 202/400/404/503,
     messages tested in `tests/test_edition_api.py`.
   - On disk: `~/.omniocr/sessions/{hash}.json` keys; `models/promoted/promoted.json`
     keys; `models/manifest.json` keys; SQLite `corrections` schema
     (`accepted INTEGER`); MLflow run/param/metric names.
   - Metrics label values: `outcome="completed"|"failed"` (pages),
     `"done"|"failed"` (jobs).
   - CLI exit codes `0/1/2/3` and existing `--json` keys.
   - `PageFailure.error_type` values (`"EmptyPage"` is string-matched by the CLI).
2. **Additive only on the wire.** New information arrives as *new optional fields*
   (`reason`, `stage`, `pages_failed`), never as changed meanings of old ones.
3. **Faithfulness rule untouched.** No machine may gain a transition that mutates
   recognised text; suggestion/correction machines only *record* human decisions.
4. **Every phase is test-first:** characterise current behaviour with a test, then
   refactor, then add the fix as a separately named failing→passing test.
5. **All CI gates green per PR:** `pytest --cov-fail-under=80`, `ruff check`,
   `ruff format --check`, `mypy --strict` (core and editions/scripts),
   `scripts/check_layering.py`, `scripts/check_license_isolation.py`. No new
   `type: ignore`.
6. **Layering:** state enums for domain entities live in `domain/`; job state stays in
   `ports/` (already there); UI-only session state stays in `editions/desktop`.
   `scripts/check_layering.py` must pass unchanged.
7. **One PR per machine (or per tight group).** Each PR revertible on its own.

---

## 3. The shared building block (Phase 0)

New module `packages/omniocr/src/omniocr/domain/state.py` — pure, dependency-free:

```python
from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar

S = TypeVar("S", bound=StrEnum)

# A value carried in Err, never raised: a frozen dataclass subclassing Exception
# breaks when Python sets __traceback__ on raise.
@dataclass(frozen=True, slots=True)
class IllegalTransition:
    entity: str
    source: str
    target: str

def transition(entity: str, table: Mapping[S, frozenset[S]], source: S, target: S) -> Result[S, IllegalTransition]:
    """Return Ok(target) if `source -> target` is declared in `table`."""
```

Plus, for each machine, next to its enum:

```python
JOB_TRANSITIONS: Final[Mapping[JobState, frozenset[JobState]]] = MappingProxyType({...})
```

**As built (Phase 0):** `machine()` validates a table at import time and returns a plain
`MachineSpec` of strings (so the registry needs no `Any`), with an `external` set for
states entered from outside the machine (e.g. `JobState.NOT_FOUND`, reached by TTL
expiry, not by a caller). `ALL_MACHINES` lives in
`omniocr/composition/state_machines.py`, not in `domain/`: the machines are owned by
`ports`, `application` and `infrastructure`, and only the composition root may import
them all without breaking `scripts/check_layering.py`.

And one generic contract test, `tests/test_state_machines.py`, parametrised over the
registry tuple `ALL_MACHINES` (entries added per phase):

- every state appears as a key in its table (no orphan states);
- every target is a member of the enum;
- terminal states map to `frozenset()`;
- every state is reachable from the declared initial state;
- for `StrEnum`s replacing literals: `Enum("old-literal") is Member` for every legacy
  value (proves wire compatibility).

Generated catalog (AX/DX): `scripts/gen_state_docs.py` walks `ALL_MACHINES` and
writes `docs/STATE_MACHINES.md` — one Mermaid `stateDiagram-v2` + state table per
machine, with the owning file. CI runs it with `--check` (fails if the committed doc
is stale), the same pattern as the existing `scripts/check_*.py` gates.
`AGENTS.md` and `CLAUDE.md` each get one line pointing to it.

Observability hook (DX/AX): `transition()` callers log one structured line,
`state_transition entity=<e> from=<a> to=<b> [reason=<r>]`, through the existing
`infrastructure/logging.py` logger in the infrastructure/application caller (the
domain function itself stays pure).

**Exit criteria:** module + generic test + generator + `--check` gate merged with
an empty `ALL_MACHINES`; coverage unchanged; no behaviour change.

---

## 4. Per-machine remediation (ordered by risk × reach)

Each block: **target model → fix → UX / DX / AX gain → tests → files**.

### 4.1 M1 — OCR job (🔴, Phase 1)

- **Model:** keep `JobState` values; add `JOB_TRANSITIONS`
  (`queued→running|failed`, `running→done|failed`, `done/failed/not_found` terminal).
  Add frozen `JobStatus(state: JobState, reason: str = "", pages_total: int | None = None,
  pages_failed: int | None = None)` in `ports/interfaces.py`; `IJobQueue.state()` keeps
  its signature, new optional `IJobQueue.status()` returns `JobStatus` (default impl
  wraps `state()`), so fakes in tests keep working.
- **Fixes:**
  1. Unify unknown-status fallback: both queues map an unknown backend status to
     `FAILED` with `reason="unknown backend status <x>"` and log a warning
     (defect 1). Cloud: treat `PENDING` as `NOT_FOUND` only when the upload file is
     also absent — otherwise keep `QUEUED` (no false 404s).
  2. `done` + missing file → `/ocr/status` adds `reason="result missing"`;
     `/ocr/result` status code per §7 decision 1 (default: unchanged 404).
  3. Worker records failure reason: RQ `job.meta["reason"]`, Celery task result
     info; partial success writes `pages_failed` to job meta (defect 3).
  4. Worker `outcome` uses `JobState.DONE/FAILED` values (identical strings,
     metrics unchanged).
- **UX:** clients see *why* a job failed and whether pages were skipped; no more
  eternal "queued". **DX:** one table answers "can a job go from failed to running?".
  **AX:** `/ocr/status` JSON gains `reason`, `pages_total`, `pages_failed` — an
  agent polling a job can decide to retry vs. report.
- **Tests:** extend `tests/test_edition_api.py` — RQ `_STATES` mapping fully
  parametrised (currently untested), Celery `RECEIVED/RETRY/REVOKED`, unknown-status
  fallback both editions, done-but-file-missing, reason passthrough; `tests/test_run_job.py`
  — reason recorded, partial-page count.
- **Files:** `ports/interfaces.py`, `editions/common/{api,worker}.py`,
  `editions/{server,cloud}/queue.py`, `editions/cloud/tasks.py`, `editions/server/worker.py`.

### 4.2 M2 + M16 — Review session (🔴, Phase 2)

- **Model:** `ReviewPhase(StrEnum)`: `empty → ocr_running → reviewing → empty`,
  plus `ocr_failed` (new, reachable from `ocr_running`, retry → `ocr_running`).
  `SaveState(StrEnum)`: `clean | dirty | saved`. `LineStatus(StrEnum)`:
  `raw | edited | accepted` derived by one function from the two stores — the
  stores themselves and the session JSON format stay as-is.
  Pure logic goes in `infrastructure/review.py` (already the home of `UndoLog`,
  `ReviewLine`), so it is unit-testable without Streamlit.
- **Fixes:** restore uses `.get` everywhere and restores `current_page`
  (defect 4); replace dead `saved` with `save_state`; `_set_line` marks `dirty`;
  "New session" with `dirty` asks for confirmation (defect 5); commit-for-training
  remembers committed `(line_id, text)` pairs for the session and skips them
  (pairs with the M3 fix); `_commit_edit`/accept clears the line from the other
  store so a line is never in both (removes shadowed stale edits); an OCR exception →
  `ocr_failed` with message and a Retry button instead of silently re-running on the
  next rerun.
- **UX:** visible "Unsaved changes" badge, no silent work loss, resume on the right
  page, a real error state with retry. **DX:** UI branches on one enum instead of
  `review_document is None and uploaded_file is not None`. **AX:** `UndoLog`
  sentinel fixed (`_ABSENT` object instead of `None`), session phase readable in
  AppTest by one key.
- **Tests:** `tests/test_review_ui.py` (AppTest): restore of a session file missing
  keys, page restore, dirty→confirm on New session, double commit appends once,
  edit-then-accept leaves line in one store, OCR failure shows retry.
  `tests/test_review.py`: `LineStatus` derivation, `UndoLog` with empty-string previous.
- **Files:** `editions/desktop/review_ui.py`, `infrastructure/review.py`.

### 4.3 M3 — Correction lifecycle (🔴, Phase 2, same PR series as M2)

- **Model:** keep `accepted: bool` and the SQLite column (persisted contract). Make
  "latest per `line_id` wins" a *domain* rule: `latest_per_line(corrections) ->
  tuple[Correction, ...]` in `domain/corrections.py`.
- **Fix:** `all_accepted()` returns the latest row per `line_id` only if that latest
  row is accepted (defect 6). Implemented in SQL (`MAX(corrected_at)` per `line_id`);
  the domain helper is the reference and is used by the in-memory test double.
- **⚠ Behaviour change:** training sets shrink when duplicates existed. This is the
  documented contract, but it changes model training inputs → flag in CHANGELOG and
  PR; see §7 decision 2.
- **UX/DX/AX:** a retraction actually retracts; training sees each line once;
  one helper states the rule.
- **Tests:** `tests/test_corrections_store.py` — revision supersedes, later reject
  retracts, duplicate commit yields one sample; `tests/test_corrections.py` for the helper.
- **Files:** `domain/corrections.py`, `infrastructure/corrections_store.py`.

### 4.4 M6 + M7 + M14 — Page outcome and events (🟠, Phase 3)

- **Model:** `PipelineStage(StrEnum)`: `ingest, preprocess, segment, route,
  recognise, compose, reconcile, post_correct, export`. `PageFailure` gains
  `stage: PipelineStage | None = None` (additive, defaulted — every existing
  constructor still works). `FailureKind(StrEnum)` for the *known* kinds
  (`IngestError`, `EmptyPage`, `EngineError`) — `error_type` stays `str` because the
  catch-all records `type(exc).__name__`; producers use `FailureKind.X` (same string).
  `EventType(StrEnum)`: `page_completed, page_failed, model_promoted` (+ the new
  training outcomes from §4.5); `PipelineEvent.event_type` typed as `EventType`.
- **Fix:** `_process_page` tracks the current stage in a local and puts it on the
  failure; `PageMetricsBus._OUTCOMES` keyed by `EventType`, so a rename is a mypy
  error (defect 8); CLI `_EMPTY_PAGE` becomes `FailureKind.EMPTY_PAGE`;
  `cli.py:8` docstring fixed or a real `ExitCode(IntEnum)` added with the same values.
- **UX:** "page 47 failed during *segment*: …" instead of a bare exception class.
  **DX:** typos become type errors. **AX:** `run --json` failures gain a `stage`
  key; exit codes become a documented enum.
- **Tests:** `test_core.py`, `test_pipeline_logging.py`, `test_prometheus_metrics.py`,
  `test_cli.py` (`stage` present in `--json`, `EmptyPage` still detected).
- **Files:** `domain/models.py`, `application/pipeline.py`,
  `infrastructure/prometheus_metrics.py`, `interfaces/cli.py`.

### 4.5 M4 + M5 + M10 + M13 — Training & promotion (🟠, Phase 4)

- **Model:** `TrainingOutcome(StrEnum)`: `no_corrections, no_samples, refused,
  promoted` (errors stay `Err`). `run_training` returns
  `Result[TrainingReport, TrainingError]` where `TrainingReport(outcome,
  promoted: PromotedModel | None, refusal: PromotionRefused | None)` with a
  `__post_init__` invariant: `promoted` set ⇔ `outcome is promoted`; `refusal` set ⇔
  `outcome is refused`. `RefusalReason(StrEnum)` on `PromotionRefused`
  (`wrong_split, too_few_samples, script_regressed, below_threshold,
  parent_cer_zero`). `evaluated_on` compared to `SplitName.TEST`.
- **Fix:** the only caller `scripts/train_kraken.py:168` is updated in the same PR;
  it prints the outcome and uses distinct exit codes (reusing the CLI's `0/1/2/3`
  meanings). Emit an `EventType` for every outcome, not only success. Promotion
  docstring rule order fixed to match code. `FileModelRegistry._read`: corrupt index
  → `Err`, never silently `[]` followed by an overwrite (data-loss guard).
  `KetosTrainer` clears `ketos_out/` before a run so a stale `best_*` cannot be picked.
- **UX:** "Not promoted: script `polytonic` regressed 0.4 pp" instead of a silent
  `None`. **DX:** invariant enforced by construction. **AX:** script exit code +
  one-line JSON outcome an agent can branch on.
- **Tests:** `test_training_orchestrator.py`, `test_promotion.py`,
  `test_train_kraken_script.py`, `test_model_registry.py` (corrupt index),
  `test_ketos_trainer.py` (stale best).
- **Files:** `domain/training.py`, `application/{training_orchestrator,promotion}.py`,
  `infrastructure/{model_registry,ketos_trainer}.py`, `scripts/train_kraken.py`.

### 4.6 M11 + M12 — Text-structure decisions (🟠/🟡, Phase 5)

- `JoinVerdict(StrEnum)` and `JoinSeparator(StrEnum)` (`""`, `"-"`, `" "`);
  `LineJoin.__post_init__` rejects illegal pairs (e.g. `hyphen_in_lexicon` with
  `separator=""`). The never-produced `" "` separator stays as a documented reserved
  value unless a grep shows nothing reads it.
- `SuggestionKind(StrEnum)`: `replace | flag | whole_line`, exposed as a derived
  `@property kind` on `Suggestion` — no constructor change, no stored field.
- **Faithfulness:** pure typing; no suggestion is applied differently. The existing
  `tests/test_faithfulness*.py` must pass unchanged — that is the regression gate.
- **Tests:** `test_hyphenation.py`, `test_suggestions.py`.

### 4.7 M8 + M9 + M15 + M17 + M18 — Visibility of infra state (🟡, Phase 6)

- Breaker: transitions go through one `_move(to)` that logs `state_transition`;
  `CircuitBreakerEngine.state` read-only property. Kraken: `device_fallback`
  recorded and reported. Both appear in `run --json` under
  `engines: [{name, device, breaker}]` and in `omniocr doctor --json`.
- `_model_hash="unavailable"` sentinel → `None` *only in memory*; provenance output
  keeps writing `"unavailable"` (frozen contract).
- `Settings.__post_init__` warns (not raises — raising would break existing env
  setups) on illegal combinations like `enable_vlm=1` without a key; `doctor`
  lists them as problems.
- Layout fallback and ingest: document only (catalog entry); no code change unless
  Phase 3 stage tracking already covers them.

---

## 5. Execution plan and subagent routing

Model routing per the user's instruction: **Sonnet 5.5** for design-bearing code,
**Haiku 5.5** for mechanical, low-judgement tasks. The main session (orchestrator)
owns Phase 0 design, every cross-phase decision, and final review of each PR.

| Phase | Work | Agent | Why that model | Parallel? |
|---|---|---|---|---|
| 0 | `domain/state.py`, generic test, `gen_state_docs.py --check`, CI wiring | main session | defines the pattern everyone copies | — |
| 1 | M1 OCR job | Sonnet (`general-purpose`, worktree) | cross-process semantics, API contract | after 0 |
| 2 | M2+M16 review session, M3 corrections | Sonnet (worktree) | UX design + data-quality change | parallel with 1 (disjoint files) |
| 3 | M6+M7+M14 page/events/CLI | Sonnet (worktree) | touches the hot path `pipeline.py` | after 1 merged (shares `worker.py` metrics) |
| 4 | M4+M5+M10+M13 training | Sonnet (worktree) | invariant design, one external caller | parallel with 3 |
| 5 | M11+M12 enums | **Haiku** | mechanical `StrEnum` swap, existing values, faithfulness tests as guard | parallel with 3/4 |
| 6 | M8/M9/M15 visibility | **Haiku** for logging/`doctor` fields; Sonnet review | additive fields | after 3 (needs `run --json` shape) |
| per PR | characterisation-test drafting | **Haiku** | "write a test pinning current behaviour of X" | before each phase |
| per PR | review | Sonnet `ecc:python-reviewer` or `caveman:cavecrew-reviewer` | independent read, not anchored on the author | after each phase |
| final | regenerate catalog, update `AGENTS.md`/`CLAUDE.md`/`CHANGELOG.md` | **Haiku** | mechanical doc edits | last |

Each implementing agent's brief must contain: the machine's row from §1, its §4
block, the §2 non-negotiables verbatim, the exact CI commands from `CONTRIBUTING.md`,
and "commit only to your worktree branch; do not push".

Suggested PR order: `0 → (1 ∥ 2) → (3 ∥ 4 ∥ 5) → 6 → docs`.

---

## 6. Verification

Per PR:
1. Characterisation tests written first and passing on `main` before the refactor.
2. Full CI gate list (§2.5) green locally.
3. `scripts/gen_state_docs.py --check` green (catalog matches code).
4. Contract diff: `git diff` touches no frozen value from §2.1 (reviewer checklist item).
5. For edition PRs: `docker compose -f editions/server/docker-compose.yml up` smoke —
   submit, poll to `done`, fetch result; kill worker mid-job → `failed` with `reason`.
6. For the desktop PR: Streamlit run, upload a fixture from `tests/corpus/`, edit,
   observe dirty badge, New session → confirm prompt, reload → page restored.

End of programme:
- Every row of §1 is either in `ALL_MACHINES` (and the generated catalog) or marked
  "documented, no code change" with a reason.
- `rg -n '"(queued|running|done|failed|page_completed|page_failed|model_promoted|unverified|joined_in_lexicon|hyphen_in_lexicon|EmptyPage)"' packages editions`
  returns only enum definitions and tests.
- Accuracy unchanged: `omniocr eval --split test` CER identical before/after
  Phases 3 and 5 (they must not change recognised text).

---

## 7. Decisions needing the user before implementation

1. **`/ocr/result` for `done` + missing file:** keep 404 (`"Result file not found"`),
   or move to 410 `"Result expired"`? 410 is clearer but a new status code on a
   public route. Default in this plan: keep 404, add `reason` to `/ocr/status`.
2. **Correction de-duplication (§4.3)** changes training inputs for existing
   databases. Ship as the default, or behind a one-release flag?
3. **Job cancellation** (`DELETE /ocr/{id}` → `cancelled`) is a missing state, not an
   implicit one. Out of scope here unless requested.

## 8. Explicitly out of scope / noted only

- `CachingEngine` (`resilience.py:113-172`) is not wired anywhere — candidate for
  deletion, separate change.
- `_CALAMARI_AVAILABLE` / `_VLM_AVAILABLE` (`composition/desktop.py:53-66`) are written
  but never read — dead state, delete in Phase 6 if still unused.
- `prototype/ocr.py` — reference code, not touched.
- SQLite `for_document` `LIKE 'doc%'` prefix collision and connection thread-safety —
  real issues but not state machines; file separately.

## 9. Documentation ↔ code inconsistencies found during the survey

Per CLAUDE.md rule 8, the code's conventions were followed in this plan.

1. `interfaces/cli.py:8` says "see ``ExitCode``"; no `ExitCode` exists — exit codes
   are bare module ints (`cli.py:52-55`).
2. ADR-006 (`docs/adr/README.md`, `docs/adr/003-threadpoolexecutor-parallelism.md`)
   says the pipeline "collects all RawPages from the source" before submitting; the
   code bounds work to `2 * max_workers` pages in flight (`application/pipeline.py:276-277`).
   The ADR file is also named `003-…` but titled "ADR 006".
3. `domain/training.py:3-6` says "the router accepts ``PromotedModel``"; the router
   says checkpoints are not routed per line (`application/router.py:4`).
4. `application/promotion.py` comments number rules 1, 4, 3, 2 in execution order
   (`:58,64,77,89`); the docstring orders them differently, and the parent-CER-zero
   refusal is unnumbered.
5. `domain/corrections.py:24-25` defines revision-by-later-timestamp; the store's
   `all_accepted()` (`corrections_store.py:125-138`) ignores it and returns every
   accepted row.
6. `domain/models.py:266` documents a `" "` plain-wrap `LineJoin.separator` that no
   code produces.
7. ADR-005 says server/cloud "use event-driven updates"; in practice pipeline events
   reach only Prometheus metrics — the job API exposes no progress.
8. `editions/desktop/review_ui.py:215` declares a `saved` session key that nothing
   uses.
