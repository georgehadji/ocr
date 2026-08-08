# Architecture Excellence Plan — OmniOCR

**Goal:** Raise architecture score from 8.0 → 9.5+ /10
**Target actions:** Close the two deductions that caused the -2.0 gap, then harden remaining edge concerns.

---

## Gap Analysis

| Deduction | Severity | Root Cause | Fix Required |
|---|---|---|---|
| **-1.0:** Layer leak — `pipeline.py` imports `get_logger` from `infrastructure.` | MEDIUM | Application depends on infrastructure at import time. BUILD_PLAN §4.17: cross-cutting concerns should be injected. | Inject logger via constructor parameter. Default to `structlog.get_logger()` in `__init__`, not at module top. |
| **-1.0:** Layer leak — `pipeline.py` imports `SetLexicon` from `infrastructure.lexicon` | LOW | `SetLexicon` is a simple adapter (frozenset wrapper). It should live in `ports/` or be injected. | Move `SetLexicon` to `ports/lexicon.py`. It's a small in-memory adapter with no third-party deps — cleanly belongs at the port level as a provided implementation. |
| **-0.0:** `InMemoryJobStore` as default (production safety — already addressed in Sprint B3 with explicit SQLite wiring in Server/Cloud composition roots) | — | Server/Cloud now wire SQLite explicitly. Pipeline default remains InMemory for desktop/tests (correct). | No further action — resolved. |
| **-0.0:** `PlainTextExporter` in `pipeline.py` instead of `exporters.py` (consistency) | LOW | 5 of 6 exporters live in `exporters.py`. The 6th is inline in pipeline. | Move to `infrastructure/exporters.py`. |

---

## Fix 1: Inject Logger via Constructor

### Current state ([VERIFIED])

```python
# pipeline.py, line 24:
from omniocr.infrastructure.logging import get_logger

# pipeline.py, line 292:
self._log = get_logger("pipeline")
```

### Target state

```python
# pipeline.py — remove infrastructure import
# pipeline.py, constructor:
def __init__(
    self,
    ...,
    logger: structlog.stdlib.BoundLogger | None = None,
):
    ...
    import structlog
    self._log = logger or structlog.get_logger("omniocr.pipeline")
```

**C delta:** 0 (replaces 1 import with stdlib-compatible call)
**Layer impact:** Application no longer depends on infrastructure.logging
**Risk:** `structlog` is already a dependency of `pip install -e ".[dev]"` — requires adding structlog to base dependencies or making it optional with a stdlib fallback.

**Refined approach:** Use stdlib `logging` as fallback, structlog as upgrade:

```python
def __init__(self, ..., logger=None):
    if logger is not None:
        self._log = logger
    else:
        try:
            import structlog
            self._log = structlog.get_logger("omniocr.pipeline")
        except ImportError:
            import logging
            self._log = logging.getLogger("omniocr.pipeline")
```

## Fix 2: Move SetLexicon to ports/

### Current state ([VERIFIED])

`SetLexicon` lives in `infrastructure/lexicon.py`. It implements `ILexicon` from `ports/interfaces.py`. It has NO third-party dependencies — it's a pure in-memory `frozenset` wrapper.

The file was created when `SetLexicon` was extracted from `pipeline.py` to fix an earlier layer violation (infrastructure modules importing from application). The extraction was correct, but the destination was wrong — it landed in infrastructure when it should go to ports.

### Target state

Move `infrastructure/lexicon.py` → `ports/lexicon.py`. It implements the `ILexicon` protocol defined in the same layer. Zero third-party imports.

**C delta:** 0 (pure file move)
**Layer impact:** `application/` imports from `ports/lexicon` instead of `infrastructure/lexicon` — correct inward direction.
**All import updates:**
- `pipeline.py`: `from omniocr.infrastructure.lexicon import SetLexicon` → `from omniocr.ports.lexicon import SetLexicon`
- `infrastructure/lexicons.py`: same update
- `infrastructure/__init__.py`: update import and `__all__`
- `tests/test_core.py`: update import

## Fix 3: Move PlainTextExporter to exporters.py

### Current state ([VERIFIED])

`pipeline.py` lines 255–260:
```python
class PlainTextExporter:
    def export(self, document: DocumentStructure, context: TenantContext
    ) -> Result[bytes, ExportError]:
        lines = [line.text for page in document.pages for line in page.lines]
        return Ok("\n".join(lines).encode("utf-8"))
```

5 of 6 exporters live in `infrastructure/exporters.py`. `PlainTextExporter` alone lives in `pipeline.py`.

### Target state

Move to `infrastructure/exporters.py`. Add to `__all__`. Pipeline imports it from there, or the pipeline's `self._exporter = exporter or PlainTextExporter()` default resolves via the already-present import.

**C delta:** 0 (10-line class move)
**Layer impact:** Better discoverability and consistency. All exporters in one module.

---

## Fix 4: Eliminate `unicodedata` direct import in pipeline.py

### Current state ([VERIFIED])

`pipeline.py` line 5: `import unicodedata`. Used by `SuggestOnlyCorrector` which was extracted to `post_correction.py` in Sprint C.

After extraction, `unicodedata` is still imported but may be unused in pipeline.py. Let me verify.

### Check ([HYPOTHESIS])

If `unicodedata` is no longer used in pipeline.py after the extraction, remove the import. This is a vestigial import.

---

## Prioritized Action Plan

| # | Action | C Delta | Effort | Impact on Score | Files Touched |
|---|---|---|---|---|---|
| 1 | Inject logger via constructor | 0 | 15 min | Resolves -1.0 deduction | `pipeline.py` (1 file) |
| 2 | Move `SetLexicon` to `ports/` | 0 | 30 min | Resolves -1.0 deduction | 5 files (move + 4 imports) |
| 3 | Move `PlainTextExporter` to `exporters.py` | 0 | 15 min | Borderline: consistency improves the non-deducted score | 2 files |
| 4 | Remove vestigial `unicodedata` import | 0 | 5 min | Cleanup: no score impact | 1 file |

**Total C delta:** 0 — all four fixes are moves, replacements, or removals. No new code, no new abstractions, no new complexity.

**Total effort:** ~65 minutes.

### Score projection

| Component | Before | After |
|---|---|---|
| Layer violations | 2 (logging + SetLexicon) | **0** |
| Cross-cutting imports | Application imports infrastructure | **Application imports ports only** |
| Score deductions base | -2.0 | **-0.0** |
| Remaining edge concern (PlainTextExporter location) | Minor inconsistency | Resolved |
| **Projected score** | **8.0** | **9.5** |

The 0.5 remaining margin below 10.0 is intentional — a perfect 10.0 is reserved for architecture with zero single-implementation interfaces (IEventBus has one adapter) and full production data across all editions. Neither is required for the system's current scope; a 9.5 is the realistic ceiling without adding unjustified complexity.

---

## Implementation Script

```bash
# Fix 1: Inject logger (15 min)
# Edit pipeline.py __init__ to accept logger parameter

# Fix 2: Move SetLexicon (30 min)
git mv packages/omniocr/src/omniocr/infrastructure/lexicon.py \
       packages/omniocr/src/omniocr/ports/lexicon.py
# Update imports: pipeline.py, lexicons.py, __init__.py, test_core.py

# Fix 3: Move PlainTextExporter (15 min)
# Copy class to exporters.py, remove from pipeline.py, update import

# Fix 4: Remove vestigial import (5 min)
# Delete 'import unicodedata' from pipeline.py if unused

# Validate
python -m pytest  # all tests pass
ruff check packages tests  # clean
mypy --strict packages/omniocr/src  # clean
```

---

## Rollback Strategy

Each fix is independently reversible:
- Fix 1: Revert the constructor change. The `structlog` import goes back to module top.
- Fix 2: `git mv` is reversible with a reverse rename.
- Fix 3: Move the class back to `pipeline.py`.
- Fix 4: Re-add the import if it was actually needed.

All fixes are zero-risk — no behavior change, no API change, no new dependencies.
