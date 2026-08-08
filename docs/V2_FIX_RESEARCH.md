# OmniOCR v2 — Fix Research Report

Date: 2026-07-28  
Source: `implementation_audit_report.md` §7 Required Corrections (FIX-1 through FIX-8)

---

## FIX-1 🔴 CRITICAL — `evaluation.py` never invokes the engine

### Root cause
`evaluation.py:52` hardcodes `""` as the hypothesis:
```python
script_texts.setdefault(page.script, []).append((gt_text, ""))
```
The engine's `extract()` method is never called. CER always comes out 1.0.

### Research: how engine.extract() is called in the codebase

**Interface** (`ports/interfaces.py:36-41`):
```python
class IOCREngine(Protocol):
    name: str
    def extract(self, page: RawPage, context: TenantContext) -> Result[Sequence[OCRBlock], EngineError]
```

**Pipeline usage** (`pipeline.py:333-336`):
```python
extracted = engine.extract(processed_page, context)
engine_results[engine_key] = (
    extracted.value if isinstance(extracted, Ok) else ()
)
```

**Test usage** (`test_engine_accuracy.py:111-113`):
```python
result = engine.extract(_FixturePage(load_fixture_image_bytes(fixture_id)), context)
assert result.is_ok()
return _normalize(" ".join(block.text for block in result.value))
```

**Pattern:** `engine.extract(raw_page, tenant_context) → Result → .value → join blocks`

### Fix plan

1. Add a minimal `_CorpusRawPage` class (like `_FixturePage` in tests, `InMemoryPage` in pipeline):
```python
class _CorpusRawPage:
    __slots__ = ("number", "content", "width", "height")
    def __init__(self, image_bytes: bytes) -> None:
        self.number = 1; self.content = image_bytes
        self.width = 1; self.height = 1
```

2. Inside `evaluate()`, for each page: read image bytes → wrap → call `engine.extract()` → join block texts:
```python
raw_page = _CorpusRawPage(page.image_path.read_bytes())
result = engine.extract(raw_page, context)
if not result.is_ok():
    return Err(f"engine failed: {result.error}")
hypothesis = " ".join(block.text for block in result.value)
```

3. Pass `engine.name` directly (FIX-8 folds in).

**Files changed:** `application/evaluation.py` only.

---

## FIX-2 🔴 CRITICAL — Router promoted-model branches are no-ops

### Root cause
`router.py:42` and `router.py:71` both contain `pass` where they should return engines. The router consults `IModelRegistry` but has no `engine_name → engine_instance` mapping.

### Research: how routing works

**Pipeline** (`pipeline.py:327-330`):
```python
engines = self._router.route(segment, context)
for engine in engines:
    extracted = engine.extract(processed_page, context)
```
The router must return actual `IOCREngine` **instances** — there's no second resolution step.

**PromotedModel → engine bridge:** `PromotedModel.model_ref.engine` is a string like `"kraken"`. Each engine has `.name` (e.g. `KrakenEngine.name = "kraken"`). So: `promoted.model_ref.engine` ↔ `engine.name`.

### Fix plan

1. Add `engine_map: Mapping[str, IOCREngine]` parameter to both `ScriptRouter` and `RegistryAwareRouter`.

2. In `route()`: replace `pass` with:
```python
if promoted is not None and self._engine_map:
    engine_name = promoted.model_ref.engine
    engine = self._engine_map.get(engine_name)
    if engine is not None:
        return (engine,)
```

3. In `composition/desktop.py` `create_ensemble_pipeline()`, build `engine_map` and pass it:
```python
engine_map: dict[str, IOCREngine] = {
    "kraken": kraken,
    "tesseract": tesseract,
}
router = ScriptRouter(by_script=by_script, default=default, engine_map=engine_map)
```

**Caveat:** The promoted model's specific checkpoint path (from `ModelCandidate.path`) is lost in `PromotedModel` (which only has `model_ref`). The engine returned will use its originally-configured model, not the promoted checkpoint. This is a deeper issue, but fixing the `pass` is a strict improvement — currently promoted models are completely ignored.

**Files changed:** `application/router.py`, `composition/desktop.py`.

---

## FIX-3 🟠 HIGH — `alto_training.py` imports but never uses `AltoXmlExporter`

### Root cause
`alto_training.py:33` instantiates `self._alto_exporter = AltoXmlExporter()` but `export()` never calls it. The plan §4.10 says "reusing v1's `AltoXmlExporter`", but the current `export()` just copies images and writes `train.json`.

### Research: what ketos actually consumes

`ketos train` accepts `--train train.json` which is a simple JSON manifest:
```json
[{"image": "images/line-1.png", "text": "κεφάλαιον"}, ...]
```
ALTO XML is consumed by `ketos extract` (for segmentation), not `ketos train`.

### Fix plan (Option B — safer, simpler)

The JSON manifest IS the correct format for ketos training. The error is the docstring and the dead import.

1. Remove unused import and instantiation: delete `from omniocr.infrastructure.exporters import AltoXmlExporter` and `self._alto_exporter = AltoXmlExporter()`.

2. Update docstring to reflect what it actually does (JSON manifest, not ALTO XML).

3. Keep the JSON export path as-is — it's correct for ketos.

**Files changed:** `infrastructure/alto_training.py` only.

---

## FIX-4 🟠 HIGH — `cast(IOCREngine, ...)` in orchestrator suppresses protocol mismatch

### Root cause
`_CandidateEngine` and `_ParentEngine` have `.name` but no `.extract()`, so they don't satisfy `IOCREngine`. Lines 147 and 154 use `cast()` to silence the type checker.

### Research: what IEvaluator actually uses from `engine`

All three `IEvaluator` implementations only ever read `engine.name`:
- `_CorpusEvaluator.evaluate()` — `getattr(engine, "name", "unknown")`
- `_NoOpEvaluator.evaluate()` — `getattr(engine, "name", "unknown")`
- `evaluation.evaluate()` — `engine.name if hasattr(engine, "name") else "unknown"`

None of them call `engine.extract()`. The `evaluate()` function in `evaluation.py` currently doesn't either (it compares against `""`), but FIX-1 will change that — after FIX-1, `evaluate()` WILL call `engine.extract()`.

### Fix plan

After FIX-1, `evaluation.evaluate()` will call `engine.extract()`. So `_CandidateEngine` and `_ParentEngine` need real `extract()` stubs:

```python
class _CandidateEngine:
    def __init__(self, candidate: ModelCandidate) -> None:
        self.candidate = candidate
        self.name = f"kraken:{candidate.path}"

    def extract(self, page, context):
        raise NotImplementedError("_CandidateEngine is an evaluation placeholder")

class _ParentEngine:
    def __init__(self, parent: ModelRef) -> None:
        self.parent = parent
        self.name = f"kraken:{parent.model_name}"

    def extract(self, page, context):
        raise NotImplementedError("_ParentEngine is an evaluation placeholder")
```

Remove `cast` from the import line. Remove the two `cast()` calls on lines 147 and 154.

**Files changed:** `application/training_orchestrator.py` only.

---

## FIX-5 🟡 MEDIUM — No `[training]` extra in pyproject.toml

### Fix plan

Add to `[project.optional-dependencies]`:
```toml
training = ["kraken>=6.0", "mlflow>=2.20", "Pillow>=10.0"]
```

`Pillow` is included because `PilLineCropper` needs it. `kraken` for `KetosTrainer`. `mlflow` for `MlflowModelRegistry`.

**Files changed:** `pyproject.toml` only.

---

## FIX-6 🟡 MEDIUM — IEvaluator protocol vs evaluation.py signature mismatch

### Root cause

| Source | Signature |
|---|---|
| `IEvaluator` protocol | `evaluate(engine, split) → Result[EvaluationReport, TrainingError]` |
| `evaluation.evaluate()` | `evaluate(engine, pages, split) → Result[EvaluationReport, str]` |
| `_CorpusEvaluator.evaluate()` | `evaluate(engine, split) → Result[EvaluationReport, str]` |

Two mismatches: (a) the function takes `pages` but the protocol doesn't, and (b) error types differ (`str` vs `TrainingError`).

### Fix plan

1. **Keep the protocol signature** `(engine, split)` — it's simpler and both existing adapters use it. The `_CorpusEvaluator` fetches pages from its own `ICorpusRepository`.

2. **Fix the `evaluation.py` function** to have the same signature. After FIX-1, the function needs `pages` because it calls `engine.extract()` on each page image. So the function keeps its 3-param signature (`engine, pages, split`) and the `_CorpusEvaluator` adapter bridges the gap by fetching pages and passing them.

3. **Fix the return type**: Change `evaluation.evaluate()` to return `TrainingError` instead of `str`:
```python
def evaluate(...) -> Result[EvaluationReport, TrainingError]:
    if not pages:
        return Err(TrainingError("no pages provided for evaluation"))
```

4. **Fix `_CorpusEvaluator` and `_NoOpEvaluator`** return types similarly.

5. **Also fix `alto_training.py`** return type (FIX-7): `Result[Path, TrainingError]`.

**Files changed:** `application/evaluation.py`, `composition/training.py`, `infrastructure/alto_training.py`.

---

## FIX-7 🟡 MEDIUM — `alto_training.py` return type mismatch

### Fix plan

Change `Result[Path, str]` → `Result[Path, TrainingError]`. All string error messages become `TrainingError(str_msg)`.

**Files changed:** `infrastructure/alto_training.py` only. (Rolled into FIX-3.)

---

## FIX-8 🟢 LOW — Redundant `hasattr(engine, "name")`

### Fix plan

`IOCREngine` protocol guarantees `.name: str`. Replace:
```python
engine.name if hasattr(engine, "name") else "unknown"
```
with:
```python
engine.name
```

**Files changed:** `application/evaluation.py` only. (Rolled into FIX-1.)

---

## Execution order (by dependency)

```
1. FIX-5 (pyproject.toml)          — independent
2. FIX-3+FIX-7 (alto_training.py)  — independent
3. FIX-2 (router.py + desktop.py)  — independent
4. FIX-4 (orchestrator)            — depends on FIX-1 understanding
5. FIX-1+FIX-6+FIX-8 (evaluation)  — combined into one evaluation.py rewrite
```

## Files touched summary

| File | Fixes | Nature |
|---|---|---|
| `application/evaluation.py` | FIX-1, FIX-6, FIX-8 | Rewrite (engine call + error types + hasattr) |
| `application/router.py` | FIX-2 | Add `engine_map` param, replace `pass` |
| `composition/desktop.py` | FIX-2 | Build `engine_map`, pass to router |
| `application/training_orchestrator.py` | FIX-4 | Add `extract()` stubs, remove `cast` |
| `infrastructure/alto_training.py` | FIX-3, FIX-7 | Remove unused AltoXmlExporter, fix return type |
| `composition/training.py` | FIX-6 | Fix `_CorpusEvaluator` and `_NoOpEvaluator` return types |
| `pyproject.toml` | FIX-5 | Add `[training]` extra |
