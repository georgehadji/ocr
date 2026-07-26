# Implementation Audit Report — OmniOCR

**Audit date:** 2026-07-26
**Commits reviewed (10 total + uncommitted WIP):**
| # | Commit | Description |
|---|---|---|
| 1 | `9ed34d3` | feat: build production OCR core |
| 2 | `93ed8c0` | feat(core): harden phase one rails |
| 3 | `bbbde58` | feat(core): add OCR regression metrics |
| 4 | `0438151` | feat(preprocess): add adaptive binarization |
| 5 | `73536c4` | feat(core): add settings and pipeline events |
| 6 | `d384268` | feat(resilience): add circuit breaker and cache |
| 7 | `6321ba6` | feat(layout): add Kraken line segmentation |
| 8 | `b47d61b` | fix(core): align page OCR and typing |
| 9 | `cf79470` | fix(types): tighten page protocol contracts |
| 10 | `55f0a75` | feat(core): add provenance and integrity rails |
| — | *(uncommitted)* | WIP: upload validation + license isolation CI |

**Implementation plan:** `docs/BUILD_PLAN.md` (Phases 0–6)
**Architecture reference:** `docs/ARCHITECTURE.md`

---

## 1. Executive Summary

The ten-commit sequence delivers a **mature Phase 0/1 foundation** with systematic type-hardening, real layout segmentation, provenance tooling, and the beginnings of a security/regression harness. Commits 9–10 convert the entire pipeline to `isinstance`-based type narrowing for mypy strict-mode, add `mypy --strict` to CI, introduce model hash verification, PAGE-XML export, and page-level timing instrumentation. Uncommitted work adds file-upload validation and a GPL license-isolation CI check.

**Key metrics:** 46 tests passing (up from 40), **87%** overall line coverage (steady), ruff clean, `mypy --strict` now enforced in CI, license isolation check in CI.

**Resolved this cycle:** `mypy --strict` in CI (C6), model hash verification utilities, upload validation, PAGE-XML export, page timing, pipeline-wide type narrowing.

**Remaining Phase 1 gaps:** CER regression corpus still scaffold-only (the one remaining High). No default polytonic PDF font. Legacy migration not done. Minor: `__all__` double assignment, Tesseract hash `"unknown"`, no Windows/bandit/pip-audit in CI.

**Verdict: APPROVED WITH CHANGES** — 3 gaps closed this cycle, 1 High remains, no new defects.

---

## 2. Plan Compliance Matrix

| Plan Item | Status | Evidence | Notes |
|---|---|---|---|
| **Phase 0 — Foundation** | | | |
| Domain models, Result, ports | ✅ Complete | 12 ports; `PipelineEvent.duration_ms` added | |
| `pyproject` + extras | ✅ Complete | | |
| CI skeleton + test harness | ✅ Complete | Matrix 3.11/3.12, 80% cov gate, ruff, **mypy, license isolation** | mypy + license check added this cycle |
| Pre-commit hooks | ✅ Complete | | |
| `mypy --strict` clean | ✅ **Resolved** | `mypy --strict --ignore-missing-imports --follow-imports=skip` in CI | Commit 10; closes C6 from prior audit |
| No-op pipeline returning Ok | ✅ Complete | | |
| **Phase 1 — Printed-Greek Core (MVP)** | | | |
| TesseractEngine adapter | ✅ Complete | | |
| KrakenEngine adapter | ✅ Complete | | |
| PyMuPDF streaming ingest | ✅ Complete | | |
| Preprocess chain | ✅ Complete | Grayscale + Sauvola + Passthrough; now returns `ImagePage` explicitly | |
| Script router | ✅ Complete | | |
| Reconcile | ✅ Complete | | |
| Faithful post-correction | ✅ Complete | NFC, dangling marks, lexicon, ligatures | |
| Desktop composition root | ✅ Complete | `KrakenLayoutAnalyzer` wired | |
| Layout segmentation | ✅ Complete | `KrakenLayoutAnalyzer` in `kraken.py` | |
| Export: TXT | ✅ Complete | | |
| Export: Markdown | ✅ Complete | | |
| Export: ALTO XML | ✅ Complete | | |
| Export: PAGE-XML | ✅ **Complete** | `PageXmlExporter` with coords, confidence, provenance | New in commit 10 |
| Export: Searchable PDF | ✅ Improved | No default font | Medium gap |
| Export: DOCX | ✅ Complete | | |
| Retire `ocr.py` | ❌ Not done | | |
| Kraken > Tesseract CER on fixture | ❌ Not started | `RegressionBaseline` + `regression_exceeded()` exist; no fixture baselines | |
| Faithfulness test | ✅ Complete | | |
| ≥80% coverage enforced | ✅ Complete | 87% | |
| CER/WER metrics | ✅ Complete | Now with `RegressionBaseline` + `regression_exceeded()` | |
| CER baselines committed | ⚠️ Scaffolded | `baselines.json` = `{}`; regression gate functions ready | |
| Per-page failure isolation | ✅ Complete | Now with page timing in events | |
| `ILexicon` port | ✅ Complete | | |
| **Cross-cutting (BUILD_PLAN §4)** | | | |
| Circuit breaker + cache | ✅ Complete | | |
| `IEventBus` implementation | ✅ Complete | Pipeline events now include `duration_ms` | |
| Config: env-backed settings | ✅ Complete | | |
| Engine call dedup + box assignment | ✅ Complete | | |
| `isinstance` type narrowing | ✅ **Complete** | Applied throughout pipeline, resilience, preprocess, export | Commit 9 |
| Model hash verification | ✅ **Complete** | `sha256_file()` + `verify_model_hash()`; `models/` directory | Commit 10 |
| Upload validation | ✅ **WIP** | `validate_upload()` — magic bytes, size limit, path sanitization | Uncommitted |
| License isolation CI | ✅ **WIP** | `scripts/check_license_isolation.py` in CI | Uncommitted |
| `structlog` structured logging | ❌ Missing | | |
| `pydantic-settings` config | ⚠️ Partial | Env-backed bare dataclass — acceptable for desktop | |
| **Phase 2 — Layout & Faithful Exports** | ⚠️ Partial | Line segmentation done; region classification not started | |
| **Phase 3 — Variety Coverage** | ⚠️ Partial | Ligatures exist; no Byzantine/Pontian lexicons | |
| **Phase 4 — VLM + Calamari** | ❌ Not started | | |
| **Phase 5 — Server & Cloud** | ❌ Not started | | |
| Edition migration to `editions/` | ❌ Not done | | |
| Regression gate in CI | ❌ Missing | `regression_exceeded()` ready; no baselines, no CI job | |

---

## 3. Architecture Compliance Assessment

### 3.1 Layer Separation — ✅ PASS

Clean hexagonal architecture maintained across all 10 commits. New modules (`infrastructure/models.py`, `infrastructure/security.py` — uncommitted) correctly live in infrastructure. New exporter (`PageXmlExporter`) follows the same pattern as existing exporters. No layer violations.

### 3.2 Type Narrowing (commit 9) — ✅ Significant hardening

Commit 9 converts all `Result` handling in the pipeline, preprocessors, and exporters from `.is_ok()`/`.is_err()` to `isinstance(result, Ok)`/`isinstance(result, Err)` with `assert isinstance(result, Ok)` after the error check. This is a consistent application of the pattern introduced in commit 8. The `assert` provides a runtime safety net: if the `Result` type hierarchy is ever corrupted (e.g., a third subclass introduced), it fails loudly rather than silently producing incorrect behavior.

This makes the codebase **mypy strict-mode compatible** and the `mypy --strict` CI gate (added in commit 10) validates this on every push.

### 3.3 RawPage Protocol (commit 9) — ✅ Correctness fix

Changed from bare attribute annotations to `@property` with explicit return types:

```python
# Before (structural subtyping ambiguity)
class RawPage(Protocol):
    number: int
    content: bytes

# After (explicit interface contract)
class RawPage(Protocol):
    @property
    def number(self) -> int: ...
    @property
    def content(self) -> bytes: ...
```

This is important for structural subtyping: a class with `number: int` as a class-level annotation vs an instance attribute could satisfy the protocol differently. Properties with `...` body make the contract explicit: "any object with readable `number`, `content`, `width`, `height` attributes." Both `ImagePage` and `InMemoryPage` satisfy this without changes.

### 3.4 ImagePage Return (commit 9) — ✅ Correctness fix

`GrayscaleProcessor` and `SauvolaProcessor` changed from `type(page)(...)` to `ImagePage(...)`. This fixes a latent bug: `type(page)` would reconstruct whatever `RawPage` implementation was passed in, but the processors produce a new PNG image — `ImagePage` (from `infrastructure/ingest.py`) is the correct concrete return type. The input page type (e.g., a PDF page or an in-memory test page) should not determine the output type of image processing.

### 3.5 Model Hash Verification (commit 10) — ✅ Well-designed

`infrastructure/models.py` provides two utilities aligned with BUILD_PLAN §1.6 (reproducibility) and §9 (model pinning):

- **`sha256_file(path)`**: Streaming hash — reads in 1 MB chunks, never loads the full model into memory.
- **`verify_model_hash(path, expected)`**: Validates the digest format (64 hex chars) before comparison, case-insensitive. Raises `ValueError` on malformed input — fail-fast.

`models/README.md` documents the artifact policy: only explicitly licensed models, recorded in a reviewed manifest with hash.

**Observation:** These utilities exist but aren't yet wired into `KrakenEngine` or `TesseractEngine` adapters. That's appropriate for the current phase — the utilities are ready for when Phase 4 model pinning is implemented.

### 3.6 Regression Gate Functions (commit 10) — ✅ Foundation laid

`RegressionBaseline` (frozen dataclass, validates non-negative CER/WER) and `regression_exceeded()` (with configurable tolerance) provide the building blocks for the CI regression gate from BUILD_PLAN §8. The functions are ready; what's missing is committed baseline data and a CI job that calls them against fixture pages.

### 3.7 PAGE-XML Exporter (commit 10) — ✅ Complete

New `PageXmlExporter` produces PAGE-XML (Prima Research 2019-07-15 namespace) with:
- Per-page `imageWidth`/`imageHeight`/`imageFilename`
- Per-line polygon coordinates in `Coords` element
- Engine provenance in `UserDefined`/`UserAttribute` elements (engine, model, modelHash)
- Unicode text with confidence (normalized 0–1) and script
- Proper namespace declaration on root `PcGts` element

This completes the ALTO + PAGE-XML export pair from BUILD_PLAN §4.12 and ARCHITECTURE §6.

### 3.8 Page Timing (commit 10) — ✅ Observability foundation

`PipelineEvent` gains `duration_ms: float = 0.0`. The pipeline measures `perf_counter()` around `_process_page()` and includes elapsed milliseconds in both `page_completed` and `page_failed` events. Test updated to assert `duration_ms >= 0` instead of exact event equality — correct: timing is non-deterministic.

### 3.9 Upload Validation (uncommitted) — ✅ Security foundation

`validate_upload()` covers four attack vectors from BUILD_PLAN §6:
1. **Size limit** — rejects empty data and data exceeding `max_bytes`
2. **Path traversal** — rejects filenames with path components (`../`, `/`)
3. **Format whitelist** — only PDF, PNG, JPG, TIFF allowed
4. **Magic byte verification** — file content must match its claimed extension

Well-tested (4 assertions in one test, separate acceptance test for valid PNG). Signature returns `Result[bytes, IngestError]` — composable with the pipeline's railway error model.

### 3.10 License Isolation (uncommitted) — ✅ BUILD_PLAN §9

`scripts/check_license_isolation.py` uses regex to forbid `import calamari_ocr` or `from calamari_ocr` in the shared `packages/omniocr` source tree. Wired into CI after the mypy step. This enforces BUILD_PLAN §4.8 (GPLv3 subprocess isolation) mechanically — a PR that adds a Calamari import to the core fails CI.

---

## 4. Code Quality Findings

### 4.1 Strengths (updated)

1. **Consistent type narrowing** — `isinstance(result, Ok/Err)` + assert throughout entire pipeline
2. **Streaming model hashing** — 1 MB chunks, memory-safe for large model files
3. **PAGE-XML with full provenance** — coordinates, confidence, engine/model/hash on every line
4. **Upload validation** — magic bytes, size, path sanitization, format whitelist
5. **License isolation CI** — regex-enforced, prevents GPLv3 contamination
6. **Page timing instrumentation** — `perf_counter()` for monotonic, high-resolution measurements
7. **Explicit return types** — `ImagePage` instead of `type(page)`, correct semantics
8. **`mypy --strict` in CI** — type safety enforced on every push
9. **Regression gate building blocks** — `RegressionBaseline` + `regression_exceeded()` ready for CI

### 4.2 Open Defects

| # | Severity | File(s) | Issue | Recommendation |
|---|---|---|---|---|
| D1 | **High** | `tests/corpus/` | `baselines.json` is empty. `regression_exceeded()` is ready but no fixture baselines exist. | Add fixture pages + ground truth; compute and commit baselines; add CI regression job. |
| D2 | **Medium** | `infrastructure/exporters.py` | Searchable PDF requires explicit `font_path` for Unicode. No bundled polytonic font. | Bundle Gentium Plus (OFL). |
| D3 | **Medium** | `ocr.py`, `Cloud/Desktop/Server Edition/` | Legacy prototype + Editions not migrated. | Move to `prototype/` and `editions/`. |
| D4 | **Low** | `infrastructure/__init__.py:4,14` | `__all__` doubly assigned — first list (`PassthroughProcessor`, `Settings`, `normalize_nfc`) overwritten by second. | Merge into single list with all 12 names. |
| D5 | **Low** | `infrastructure/tesseract.py:54` | Tesseract model hash hardcoded `"unknown"`. `sha256_file()` now exists — use it. | Hash traineddata file at init using `sha256_file()`. |
| D6 | **Low** | `.github/workflows/ci.yml` | No Windows runner; no `bandit`/`pip-audit`. | Add per BUILD_PLAN §9. |
| D7 | **Low** | `composition/desktop.py` | `create_desktop_pipeline` and `create_ensemble_pipeline` still use `SingleLineLayoutAnalyzer`. | Wire `KrakenLayoutAnalyzer` consistently. |

### 4.3 Issues Resolved This Cycle

| # | Previous Severity | Issue | Resolution |
|---|---|---|---|
| C6 | Low | No `mypy` in CI | Commit 10: `mypy --strict` job added to CI |
| — | — | `type(page)` in preprocessors | Commit 9: explicit `ImagePage(...)` |
| — | — | `.is_ok()`/`.is_err()` inconsistent with type narrowing | Commit 9: full `isinstance` + assert conversion |
| — | — | `RawPage` protocol ambiguity | Commit 9: `@property` with explicit return types |
| — | — | No model hash verification | Commit 10: `sha256_file()` + `verify_model_hash()` |
| — | — | No PAGE-XML export | Commit 10: `PageXmlExporter` |
| — | — | No page timing | Commit 10: `PipelineEvent.duration_ms` + `perf_counter()` |
| — | — | No regression gate functions | Commit 10: `RegressionBaseline` + `regression_exceeded()` |
| — | — | No upload validation | Uncommitted: `validate_upload()` |
| — | — | No license isolation CI | Uncommitted: `check_license_isolation.py` in CI |

### 4.4 Improvement Opportunities

| Area | Suggestion | BUILD_PLAN ref |
|---|---|---|
| Regression | Wire `regression_exceeded()` into CI with committed baselines | §8 |
| Export | Template Method base class for exporters | §4.12 |
| Observability | Add `structlog` (event bus is there, just needs structured keys) | §4.18 |
| Config | Convert `Settings` to `pydantic-settings` when server/cloud need it | §4.17 |
| Security | Wire `validate_upload()` before `DocumentPageSource.stream()` | §6 |
| Font | Bundle Gentium Plus OFL for PDF export | §6, §4.12 |
| Hash | Wire `sha256_file()` into `TesseractEngine` and `KrakenEngine` | §4.3 |
| Layout | Wire `KrakenLayoutAnalyzer` into remaining factory functions | §4.6 |

---

## 5. Testing & Coverage Assessment

### 5.1 Test Suite Summary

**46 tests, all passing.** Coverage: **87%** overall.

| Test file | Tests | Δ | Focus |
|---|---|---|---|
| `test_core.py` | 17 | — | Domain, pipeline, checkpoint, lexicon, ligatures, isolation, events with timing, engine dedup |
| `test_exporters.py` | 6 | +1 | Markdown, ALTO, **PAGE-XML**, PDF, DOCX |
| `test_metrics.py` | 4 | +1 | CER/WER, **regression gate with tolerance** |
| `test_models.py` | 2 | **new** | SHA-256 file hashing, digest verification, malformed input |
| `test_security.py` | 2 | **new** | Upload: valid PNG, size/path/magic/format rejections |
| `test_resilience.py` | 3 | — | Retry, circuit breaker, cache |
| `test_config.py` | 3 | — | Settings defaults, env loading, validation |
| `test_layout.py` | 1 | — | Kraken line segmentation |
| `test_reconcile.py` | 2 | — | Confidence-weighted selection, empty candidates |
| `test_faithfulness.py` | 2 | — | Source text preservation, dangling marks |
| `test_ingest.py` | 3 | — | Image dimensions, grayscale, Sauvola |
| `test_kraken.py` | 1 | — | Kraken record parsing |
| `test_tesseract.py` | 1 | — | Tesseract output parsing |
| `test_router.py` | 1 | — | Script-based routing |

### 5.2 New Test Quality (commits 9–10 + uncommitted)

- **`test_models.py`**: Uses `pyproject.toml` as a real file for hash verification — practical, doesn't depend on external fixtures. Tests case-insensitive comparison and malformed digest rejection.
- **`test_metrics.py::test_regression_gate_applies_cer_and_wer_tolerance`**: Tests baseline exceedance with tolerance — correct: high-CER input fails without tolerance, passes with tolerance=1.0.
- **`test_exporters.py::test_page_xml_export_contains_coordinates_text_and_confidence`**: Parses XML output, verifies element structure, coordinates, confidence normalization (87.5 → 0.875000), and provenance user attributes.
- **`test_security.py`**: Two tests — one for valid PNG acceptance, one for four rejection scenarios (size, path traversal, magic mismatch, unsupported format) in a single parametrized-style test.
- **`test_core.py`** (updated): Pipeline events test now checks `duration_ms >= 0` instead of exact equality — handles non-deterministic timing correctly.

### 5.3 Coverage Detail

| Module | Coverage | Uncovered |
|---|---|---|
| `application/metrics.py` | 95% | Error branches in `regression_exceeded` |
| `application/pipeline.py` | 91% | Error branches, checkpoint paths |
| `infrastructure/resilience.py` | 92% | Error formatting |
| `infrastructure/models.py` | 89% | Error branches |
| `infrastructure/security.py` | 91% | Error branches |
| `infrastructure/exporters.py` | 85% | Error branches in PDF/DOCX/PAGE-XML |
| `infrastructure/preprocess.py` | 82% | Error paths |
| `infrastructure/jobs.py` | 80% | SQLite/JSON error paths |
| `infrastructure/kraken.py` | 74% | `extract()` (requires kraken), `_bounds` edge cases |
| `infrastructure/ingest.py` | 61% | DocumentPageSource (requires fitz) |

---

## 6. Risk & Regression Analysis

### 6.1 Active Risks

| Risk | Severity | Status |
|---|---|---|
| No CER regression gate | **High** | `regression_exceeded()` ready; corpus still empty |
| Polytonic font in PDF | Medium | No bundled font |
| Prototype/edition divergence | Medium | Not migrated |
| `__all__` double assignment | Low | Losing 3 export names |
| Tesseract hash `"unknown"` | Low | `sha256_file()` exists — just not wired |
| No Windows/bandit/pip-audit in CI | Low | License isolation added; remaining security tools missing |
| KrakenLayoutAnalyzer only in one factory | Low | `create_ensemble_pipeline` still uses `SingleLineLayoutAnalyzer` |

### 6.2 Technical Debt Register

| Item | Location | Impact |
|---|---|---|
| `__all__` overwritten | `infrastructure/__init__.py` | Cosmetic |
| `SuggestOnlyCorrector` in `pipeline.py` | `application/pipeline.py` | Should move to `post_correction.py` |
| `_process_page` uses exceptions for Result | `pipeline.py` | Hybrid style — works |
| No structured logging | All modules | Will become painful at scale |
| `CachingEngine` unbounded cache | `infrastructure/resilience.py` | Needs LRU for server |
| `validate_upload` not wired into ingest | Uncommitted file | Ready but not called before `DocumentPageSource` |

### 6.3 Backward Compatibility

No breaking changes. `RawPage` protocol change from attributes to properties is backward-compatible — existing `ImagePage` and `InMemoryPage` satisfy both forms. `ImagePage(...)` return type in preprocessors produces the same concrete type behavior (both are `RawPage`-satisfying objects).

---

## 7. Required Corrections

| # | Severity | File(s) | Issue | Recommendation |
|---|---|---|---|---|
| C1 | **High** | `tests/corpus/` | Empty corpus — `regression_exceeded()` ready but no baselines | Add fixture pages per script variety; compute and commit baselines; add CI regression job |
| C2 | **Medium** | `infrastructure/exporters.py` | No default polytonic PDF font | Bundle Gentium Plus (OFL) |
| C3 | **Medium** | `ocr.py`, Editions | Legacy code not migrated | Move to `prototype/` and `editions/` |
| C4 | **Low** | `infrastructure/__init__.py` | `__all__` doubly assigned — losing 3 names | Merge into single list of 12 names |
| C5 | **Low** | `infrastructure/tesseract.py` | Tesseract hash `"unknown"`; `sha256_file()` exists | Wire `sha256_file()` at init |
| C6 | **Low** | `.github/workflows/ci.yml` | No Windows runner; no `bandit`/`pip-audit` | Add per BUILD_PLAN §9 |
| C7 | **Low** | `composition/desktop.py` | `create_ensemble_pipeline` still uses `SingleLineLayoutAnalyzer` | Wire `KrakenLayoutAnalyzer` |

---

## 8. Final Verdict

### APPROVED WITH CHANGES

**Rationale:** The ten-commit sequence shows accelerating quality improvement. Commits 9–10 represent a systematic hardening pass:

- **Type safety**: Full `isinstance` + assert conversion across the pipeline, `RawPage` protocol tightened, `mypy --strict` in CI
- **Provenance**: Model hash verification, `models/` directory policy, PAGE-XML export with full provenance chain
- **Observability**: Page-level timing in pipeline events
- **Security**: Upload validation (magic bytes, size, paths), license isolation CI check
- **Regression**: `RegressionBaseline` + `regression_exceeded()` ready for CI integration

**Trajectory:**

| Metric | Audit 1 | Audit 2 | Audit 3 | Audit 4 |
|---|---|---|---|---|
| Commits | 1–4 | 1–6 | 1–8 | **1–10** |
| Tests | 30 | 38 | 40 | **46** |
| Coverage | 82% | 87% | 87% | **87%** |
| High issues | 1 | 1 | 1 | **1** |
| Medium issues | 4 | 4 | 3 | **3** |
| Low issues | 5 | 5 | 5 | **4** ⬇ |
| Resolved this cycle | — | 4 | 5 | **7** |

**The three remaining blockers for Phase 1 MVP completion:**

1. **CER regression harness** (High) — `RegressionBaseline`, `regression_exceeded()`, and `character_error_rate`/`word_error_rate` are all ready. The only missing piece is real fixture pages and committed baselines. This is the single remaining High item.
2. **Searchable PDF default font** (Medium) — Bundle Gentium Plus (OFL-licensed, polytonic-capable) and auto-apply for Greek text.
3. **Legacy migration** (Medium) — Move `ocr.py` to `prototype/` and Editions to `editions/`.

**Recommendation:** C1 is the critical path. The infrastructure is fully built — `regression_exceeded(reference, hypothesis, baseline, tolerance)` is ready. Adding 3–5 fixture pages with ground-truth text and wiring the function into CI is a contained, high-impact task. C4 (`__all__` merge) and C5 (wire `sha256_file` into TesseractEngine) are trivial fixes that should ship alongside.
