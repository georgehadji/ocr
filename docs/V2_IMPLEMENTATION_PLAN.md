# OmniOCR v2 — Implementation Plan

Companion to [V2_PLAN.md](V2_PLAN.md) (*what* and *why*). This document is *how*: the
concrete modules, paradigms, design patterns, and build order for v2.

Binding constraints, inherited and unchanged:

- **Design of record** — [ARCHITECTURE.md](ARCHITECTURE.md). Dependencies point inward:
  `interfaces → infrastructure → application → domain`.
- **v1 engineering rules** — [BUILD_PLAN.md](BUILD_PLAN.md) §2 (Functional Core / Imperative
  Shell + Hexagonal + Pipes-and-Filters), §7 tooling, §1 definition of production-grade.
- **Faithfulness is structural, not procedural** — v1 made "post-correction cannot rewrite
  text" *impossible by type* (frozen `OCRLine` + separate `Suggestion` layer). v2 applies the
  same technique to training (§4.3).
- **v2 operating rule** — nothing is done until executed against real input with the result
  recorded (V2_PLAN §1).

---

## 1. What already exists

v2 extends a working hexagon. Existing surface that must not be broken:

**Ports** (`omniocr/ports/interfaces.py`): `RawPage`, `IPageSource`, `IImageProcessor`,
`IOCREngine`, `ILayoutAnalyzer`, `IRouter`, `IReconciler`, `IPostCorrector`, `ILexicon`,
`IExporter`, `IJobStore`, `IEventBus`.

**Domain** (`omniocr/domain/models.py`): `BBox`, `Confidence`, `Script`, `RegionType`,
`ModelRef`, `EngineRun`, `Suggestion`, `PageFailure`, `PipelineEvent`, `OCRBlock`, `OCRLine`,
`OCRParagraph`, `DocumentPage`, `DocumentStructure`, `TenantContext` — all frozen, `slots=True`.

**The gap v2 must fill:** there is a `Suggestion` (machine proposal) but **no domain type for
an accepted human correction**. That absence is the root cause of defect D10 — with nowhere to
put verified ground truth, `training.py` fell back to the model's own output.

---

## 2. Additions at a glance

| Layer | New modules |
|---|---|
| `domain/` | `corrections.py`, `training.py`, `corpus.py` |
| `ports/` | `ICorrectionStore`, `ITrainingDataExporter`, `ITrainer`, `IModelRegistry`, `IEvaluator`, `ILineCropper`, `ICorpusRepository` |
| `application/` | `ground_truth.py`, `promotion.py`, `training_orchestrator.py`, `evaluation.py`, `corpus_split.py` |
| `infrastructure/` | `corrections_store.py`, `line_cropper.py`, `alto_training.py`, `ketos_trainer.py`, `mlflow_registry.py`, `model_manifest.py`, `corpus_repository.py`, `htr/` |
| `composition/` | `training.py` (a distinct composition root) |

Rewritten: `infrastructure/training.py` (defects D9, D10) and `scripts/train_kraken.py`.

---

## 3. Paradigm and pattern per module

Rationale is one line each; the detail sits in §4.

| Module | Layer | Paradigm | Pattern(s) |
|---|---|---|---|
| `domain/corrections.py` | domain | Immutable / functional | Value Object; **Smart Constructor** |
| `domain/training.py` | domain | Immutable | Value Object; Type-state |
| `domain/corpus.py` | domain | Immutable | Value Object |
| `application/ground_truth.py` | application | **Pure functional** | Builder; Specification |
| `application/promotion.py` | application | **Pure functional** | **Strategy** (policy object) |
| `application/evaluation.py` | application | Pure functional | Strategy; Template Method |
| `application/corpus_split.py` | application | Pure, deterministic | Pure partition fn (hash-keyed) |
| `application/training_orchestrator.py` | application | Imperative shell | **Pipes & Filters**; Mediator |
| `infrastructure/corrections_store.py` | infra | OOP persistence | **Repository**; Event Sourcing (append-only) |
| `infrastructure/line_cropper.py` | infra | Functional transform | Adapter; Strategy |
| `infrastructure/alto_training.py` | infra | OOP + build | **Adapter**; reuses v1 ALTO exporter |
| `infrastructure/ketos_trainer.py` | infra | OOP adapter | **Adapter** (subprocess); Facade |
| `infrastructure/mlflow_registry.py` | infra | OOP persistence | Repository; Adapter |
| `infrastructure/model_manifest.py` | infra | Declarative data | Value Object; Repository |
| `infrastructure/corpus_repository.py` | infra | OOP persistence | Repository |
| `infrastructure/htr/` | infra | OOP adapter | Strategy; Adapter |
| `composition/training.py` | composition | Declarative wiring | **Abstract Factory**; DI |

---

## 4. Module design

### 4.1 `domain/corrections.py` — the missing concept (fixes **D10** at the type level)

Paradigm: immutable value objects. Pattern: **Value Object + Smart Constructor**.

```python
@dataclass(frozen=True, slots=True)
class Correction:
    """A human-verified transcription for one line. The only ground-truth source."""
    line_id: str
    page_number: int
    original_text: str       # what the engine produced — kept for audit
    corrected_text: str      # what the human affirmed
    corrected_by: str
    corrected_at: str
    bbox: BBox
    script: Script
    accepted: bool = True    # False = explicitly rejected, never training input
```

```python
@dataclass(frozen=True, slots=True)
class GroundTruthLine:
    """Training-eligible line. Constructible ONLY from an accepted Correction."""
    line_id: str
    text: str
    bbox: BBox
    script: Script
    source_page: int

    @staticmethod
    def from_correction(correction: Correction) -> "GroundTruthLine | None":
        if not correction.accepted or not correction.corrected_text.strip():
            return None
        return GroundTruthLine(...)
```

**Why this shape.** D10 happened because `_ground_truth_text()` could silently return
`line.text`. Here there is no path from an `OCRLine` to a `GroundTruthLine` — the only
constructor takes a `Correction`. Training on raw OCR output stops being a bug you must
remember not to write and becomes a type error. Same technique v1 used for faithfulness.

An affirmed-without-edit line is still a `Correction` (`original_text == corrected_text`) —
human affirmation is the signal, not textual difference.

### 4.2 `domain/training.py` — training value objects

Paradigm: immutable. Pattern: **Value Object + Type-state**.

```python
@dataclass(frozen=True, slots=True)
class TrainingSample:
    """One line image + its transcription. Built only from GroundTruthLine."""
    image_path: Path         # a LINE crop, never a full page — see D9
    text: str
    script: Script

@dataclass(frozen=True, slots=True)
class TrainingRun:
    run_id: str
    parent_model: ModelRef
    sample_count: int
    hyperparameters: Mapping[str, str]
    started_at: str

@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """A fine-tuned model that has NOT yet earned promotion."""
    path: Path
    model_hash: str
    parent: ModelRef
    run_id: str

@dataclass(frozen=True, slots=True)
class EvaluationReport:
    model_hash: str
    per_script_cer: Mapping[Script, float]
    per_script_wer: Mapping[Script, float]
    sample_count: int
    evaluated_on: str        # split name — must be "test"

@dataclass(frozen=True, slots=True)
class PromotedModel:
    """A candidate that beat its parent on held-out data. Only this type is routable."""
    model_ref: ModelRef
    report: EvaluationReport
    improvement_pct: float
```

`ModelCandidate` → `PromotedModel` is a **type-state transition** obtainable only through
`PromotionPolicy` (§4.4). A candidate cannot be wired into the router; the router accepts
`PromotedModel`. That makes "never ship an unproven model" structural.

### 4.3 `domain/corpus.py` — corpus value objects

```python
class SplitName(str, Enum):
    TRAIN = "train"
    DEV = "dev"
    TEST = "test"

@dataclass(frozen=True, slots=True)
class CorpusPage:
    page_id: str
    image_path: Path
    ground_truth_path: Path
    script: Script
    split: SplitName
    provenance: str          # source + licence — mandatory, not optional
    is_synthetic: bool = False
```

`provenance` is required: v2 adds public-domain scans, and an unattributed page is a licensing
liability. `is_synthetic` lets the suite keep v1's rendered fixtures as pipeline smoke tests
while excluding them from accuracy baselines.

### 4.4 `application/promotion.py` — the guard that makes learning real

Paradigm: **pure functional**. Pattern: **Strategy** (policy object).

```python
class PromotionPolicy(Protocol):
    def decide(
        self, candidate: ModelCandidate,
        candidate_report: EvaluationReport,
        parent_report: EvaluationReport,
    ) -> Result[PromotedModel, PromotionRefused]: ...


class BeatsParentOnHeldOut:
    """Promote only on measured improvement over the parent, on the test split."""

    def __init__(self, min_improvement_pct: float = 1.0) -> None: ...
```

Rules, all pure and unit-testable:

1. Refuse unless `evaluated_on == SplitName.TEST`.
2. Refuse if mean CER did not improve by at least `min_improvement_pct`.
3. Refuse if **any** script's CER regressed — a model that improves Byzantine while degrading
   polytonic is not an improvement.
4. Refuse if `sample_count` is below a floor (an "improvement" over three lines is noise).
5. Every refusal returns a reason and is logged. Silent refusal is a v1-class defect.

Kept in `application` because it is a *decision*, not I/O. Testing it needs no model, no
subprocess, no MLflow — just two reports.

### 4.5 `application/ground_truth.py` — assembling training data (fixes **D9**)

Paradigm: pure functional. Pattern: **Builder + Specification**.

```python
def assemble_training_samples(
    corrections: Sequence[Correction],
    cropper: ILineCropper,
    output_dir: Path,
    spec: SampleSpecification = DEFAULT_SPEC,
) -> Result[Sequence[TrainingSample], TrainingError]:
```

D9's fix lives here: the assembler maps each `GroundTruthLine` through `ILineCropper` to a
**line-level image**, never a page. `SampleSpecification` composes filters (minimum height,
non-empty text, confidence floor, script match) as a Specification so rules combine without
conditional sprawl.

The function is pure apart from the injected cropper — given the same corrections it yields
the same manifest, which is what makes training runs reproducible.

### 4.6 `application/evaluation.py` — measuring a model

Paradigm: pure functional. Pattern: **Strategy + Template Method**.

```python
def evaluate(
    engine: IOCREngine,
    pages: Sequence[CorpusPage],
    split: SplitName,
) -> EvaluationReport:
```

Reuses v1's `application/metrics.py` (`character_error_rate`, `word_error_rate`,
`regression_exceeded`). Refuses any split other than the one requested, so a caller cannot
accidentally evaluate on training data — the classic way a training loop reports fictional
gains.

### 4.7 `application/corpus_split.py` — deterministic partitioning

Paradigm: pure, deterministic.

```python
def assign_split(page_id: str, ratios: SplitRatios = DEFAULT_RATIOS) -> SplitName:
    """Stable hash-based split. Same page_id always lands in the same split."""
```

Hash-keyed rather than random: adding pages later must not reshuffle existing ones, or every
historical baseline becomes incomparable. Property-tested for stability and distribution.

### 4.8 `application/training_orchestrator.py` — the imperative shell

Paradigm: imperative shell over a pure core. Pattern: **Pipes & Filters + Mediator**.

```
corrections → assemble samples → export training data → train → evaluate candidate
    → evaluate parent → promotion policy → register (or refuse)
```

Threads `Result` exactly like `PipelineOrchestrator`, so a failed stage short-circuits with a
typed error rather than raising. Publishes `PipelineEvent`s on the existing `IEventBus` so the
Desktop UI reports training progress through machinery that already exists.

This module sequences side effects and contains no decision logic — every judgment is
delegated to a pure collaborator (`promotion`, `evaluation`, `ground_truth`).

### 4.9 New ports

Added to `ports/interfaces.py`, following the existing `Protocol` + `Result` style:

```python
class ILineCropper(Protocol):
    def crop(self, page_image: bytes, bbox: BBox) -> Result[bytes, TrainingError]: ...

class ICorrectionStore(Protocol):
    def append(self, correction: Correction) -> Result[None, TrainingError]: ...
    def for_document(self, document_id: str) -> Sequence[Correction]: ...
    def all_accepted(self) -> Sequence[Correction]: ...

class ITrainingDataExporter(Protocol):
    def export(self, samples: Sequence[TrainingSample], out: Path) -> Result[Path, TrainingError]: ...

class ITrainer(Protocol):
    def train(self, data: Path, parent: ModelRef, params: Mapping[str, str]) -> Result[ModelCandidate, TrainingError]: ...

class IModelRegistry(Protocol):
    def register(self, model: PromotedModel) -> Result[None, TrainingError]: ...
    def for_script(self, script: Script) -> PromotedModel | None: ...
    def for_typeface(self, typeface: str) -> PromotedModel | None: ...

class IEvaluator(Protocol):
    def evaluate(self, engine: IOCREngine, split: SplitName) -> Result[EvaluationReport, TrainingError]: ...

class ICorpusRepository(Protocol):
    def pages(self, split: SplitName, script: Script | None = None) -> Sequence[CorpusPage]: ...
```

`ITrainer` returns `ModelCandidate`, never `PromotedModel` — the type system prevents a
trainer from self-certifying its own output.

### 4.10 Infrastructure adapters

- **`corrections_store.py`** — **Repository**, append-only (SQLite). Corrections are never
  updated in place; a revised transcription is a new `Correction` with a later timestamp. This
  preserves the audit trail scholarship requires and mirrors v1's suggest-only stance.
- **`line_cropper.py`** — Adapter over PIL/OpenCV, crops by `BBox` with configurable padding.
  The concrete fix for D9.
- **`alto_training.py`** — Adapter emitting ALTO for `ketos`, **reusing v1's `AltoXmlExporter`**
  rather than a parallel serializer. Preferred over hand-rolled JSON: `ketos` consumes it, it
  round-trips geometry, and ARCHITECTURE.md §6 already commits to it.
- **`ketos_trainer.py`** — subprocess **Adapter** + Facade. Kraken's CLI, not its Python API,
  keeping the heavy training stack out of the inference process (same reasoning as Calamari's
  isolation). Timeouts, resource caps, structured logs.
- **`mlflow_registry.py`** — Repository over MLflow: params, metrics, artifacts, model lineage.
- **`model_manifest.py`** — hash-pinned artifact registry (`models/manifest.json`): engine,
  name, source, licence, SHA-256. Verified via v1's existing `sha256_file()` /
  `verify_model_hash()`. Closes W1's licensing requirement.
- **`corpus_repository.py`** — Repository over `tests/corpus/` and the real-scan corpus.
- **`htr/`** — W4 manuscript adapters: segmentation strategy for manuscript layout, per-hand
  model selection.

### 4.11 `composition/training.py` — a separate composition root

Training is a **batch pipeline**, not the recognition pipeline. It gets its own root:
`create_training_pipeline(...)` wiring corrections store, cropper, exporter, trainer,
evaluator, policy, registry. Recognition composition roots stay untouched, so nothing about
training can slow or destabilise inference.

**Constraint:** the Desktop/Server/Cloud inference roots must not import training modules.
Enforce with a CI check modelled on `scripts/check_license_isolation.py`.

---

## 5. Changes to existing modules

| Module | Change | Compatibility |
|---|---|---|
| `infrastructure/training.py` | **Rewritten.** `export_ground_truth_to_kraken_json` deleted (D9 + D10). `compute_cer_improvement` moves to `application/evaluation.py` | Breaking, internal only |
| `scripts/train_kraken.py` | Rewritten against `TrainingOrchestrator` | CLI flags preserved where sensible |
| `application/router.py` | Consults `IModelRegistry` for a promoted per-script/per-typeface model, falling back to the default engine | Additive; existing constructor still valid |
| `infrastructure/review.py` | Emits `Correction` on accept/edit | Additive |
| `ports/interfaces.py` | Seven new Protocols | Additive |
| `domain/models.py` | `Script` gains manuscript variants (W4) | Additive enum members |
| `tests/corpus/` | Real scans added; `engine_baselines.json` gains a `kraken` key per fixture | Additive |

---

## 6. Testing strategy

Extends BUILD_PLAN §8. v2's rule — **executed, not imported** — drives the additions.

1. **Pure unit** — promotion policy (every refusal branch), ground-truth assembly, split
   determinism, evaluation arithmetic. No I/O, fast.
2. **Property tests** (`hypothesis`) — split stability under insertion order; `GroundTruthLine`
   never constructible from a rejected correction; crop bounds always inside page bounds.
3. **Contract tests** — every new port gets a suite run against all adapters, as v1 does for
   `IOCREngine`.
4. **Type-level regression tests** — assert the D9/D10 shapes cannot recur: no path from
   `OCRLine` to `TrainingSample`; `ITrainer` cannot produce `PromotedModel`; training samples
   reference line crops, never page images.
5. **Real-engine accuracy** — extend `test_engine_accuracy.py` with Kraken once a model lands
   (W1), keeping the existing skip-when-unavailable behaviour.
6. **End-to-end training** (the acceptance test for W3) — a tiny committed correction set runs
   the full loop and asserts a candidate is produced, evaluated, and either promoted with a
   measured delta or refused with a reason. Marked `slow`, run nightly rather than per-PR.
7. **Executed-edition traces** (W5) — VLM against recorded cassettes, Calamari as a real
   subprocess, Server/Cloud against ephemeral Redis. Each records an artifact.

Coverage stays ≥80% overall, ≥95% for `domain/` and the pure `application/` modules.

---

## 7. Build order

Sequenced so each phase produces a usable artifact and de-risks the next. W1 first because its
outcome can invalidate an architectural premise.

### Phase A — Prove Kraken (W1)
Commit a licensed Greek model + `model_manifest.py`; un-skip the comparison test; record real
Kraken CER per script into `engine_baselines.json`; route by the measured winner.
**Done when:** every script has measured CER for both engines, and routing is justified by
those numbers — including the case where Kraken *loses* and ARCHITECTURE.md §2 must change.

### Phase B — Corpus foundations (W2)
`domain/corpus.py`, `corpus_split.py`, `corpus_repository.py`; ingest real scans with
provenance; recompute baselines on real pages.
**Done when:** baselines reflect real scans and synthetic fixtures are demoted to smoke tests.

### Phase C — Corrections capture (W3, part 1 — fixes D10)
`domain/corrections.py`, `ICorrectionStore`, `corrections_store.py`; review UI emits
`Correction`.
**Done when:** a review session produces a durable, append-only correction log with provenance.

### Phase D — Training data (W3, part 2 — fixes D9)
`ILineCropper`, `line_cropper.py`, `ground_truth.py`, `alto_training.py`.
**Done when:** a correction set yields line-crop training data `ketos` accepts — verified by
running `ketos` on it, not by asserting file shape.

### Phase E — Train, evaluate, promote (W3, part 3)
`ITrainer`/`ketos_trainer.py`, `evaluation.py`, `promotion.py`, `mlflow_registry.py`,
`training_orchestrator.py`, `composition/training.py`.
**Done when:** a fine-tuned model measurably beats its parent on held-out real pages, and an
unimproved model is refused with a logged reason.

### Phase F — Per-typeface routing (W3, part 4)
`IModelRegistry` wired into `ScriptRouter`; document-level typeface tagging.
**Done when:** tagging a document routes its pages to the model fine-tuned for it.

### Phase G — Manuscript HTR (W4)
Manuscript `Script` variants, `htr/` segmentation and per-hand models, manuscript abbreviation
layer (reversible, suggest-only).
**Done when:** a manuscript page yields a usable diplomatic transcription measured against
held-out ground truth.

### Phase H — Prove the editions (W5)
Runs in parallel from Phase A onward.
**Done when:** every edition has an executed trace, not a unit test.

---

## 8. Architectural risks specific to v2

| Risk | Mitigation |
|---|---|
| Training deps (torch, MLflow) leak into the inference install | Separate composition root + `[training]` extra + CI import-isolation check (§4.11) |
| Fine-tuning degrades faithfulness guarantees | v1 byte-identity tests stay green throughout; treat any failure as blocking |
| A model is promoted on training data | `EvaluationReport.evaluated_on` + policy refusal (§4.4 rule 1) |
| Corrections silently mutate history | Append-only store; corrections are events, never updates (§4.10) |
| Per-typeface models multiply and drift | Hash-pinned manifest + registry lineage to parent |
| Manuscript work destabilises printed accuracy | Separate `Script` variants and models; printed baselines gate every PR |

---

## 9. Definition of done

v1's definition (BUILD_PLAN §1) plus V2_PLAN §8, made concrete here:

1. `mypy --strict` clean across all new modules; no `Any` in `domain/` or `application/`.
2. Every new port has contract tests against every adapter.
3. Promotion policy has a unit test per refusal branch.
4. The D9/D10 shapes are structurally unreachable, with tests asserting it.
5. Every accuracy number in any document traces to a committed script over held-out real data.
6. Training and inference remain separable — inference installs without training deps.
