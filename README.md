<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-136-success" alt="136 tests">
  <img src="https://img.shields.io/badge/coverage-87%25-yellow" alt="87% coverage">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

<h1 align="center">OmniOCR</h1>

<p align="center">
  <strong>Printable Greek Optical Character Recognition — from Modern to Byzantine</strong>
  <br>
  Faithful (diplomatic) transcription for scholarly, liturgical, and historical printed Greek.
</p>

---

## Overview

OmniOCR is a CPU-first OCR suite purpose-built for **printed Greek across every variety**: modern monotonic, polytonic, Ancient Greek (critical editions), Byzantine printed typefaces, and Pontian. It ingests large PDFs and images and exports to DOCX, Markdown, searchable PDF, TXT, ALTO, and PAGE-XML.

**Architecture:** Clean hexagonal design with a shared core library that serves three editions:

| Edition | Stack | When to use |
|---|---|---|
| [Desktop](#desktop-edition) | Streamlit review UI | Single-scholar correction workflow |
| [Server](#server-edition) | FastAPI + RQ + Redis | Headless batch processing |
| [Cloud](#cloud-edition) | FastAPI + Celery + Redis + Docker | Multi-tenant production deployment |

### Guiding principle: faithfulness over fluency

For scholarly Greek, a "plausible" reading is worse than a flagged uncertain one. Post-correction is **suggest-only** — lexicons highlight, they never rewrite. VLM output is opt-in, always grounding-checked, and never the unaudited sole source.

- **Verifiable engines** (Tesseract, Kraken) are the source of truth — every character is auditable and round-trippable to the page image.
- **VLM output** (OpenRouter) is an opt-in second opinion, reconciled against box-grounded output. Divergence raises a flag, it does not overwrite.
- **Post-correction** suggests; the human decides.

---

## Quick Start

```bash
# 1. Install the package with all optional dependencies
pip install -e ".[pdf,kraken,opencv,docx,dev]"

# 2. Run the review UI
streamlit run editions/desktop/review_ui.py
```

Upload a PDF — the pipeline runs automatically. That's it.

---

## Installation

### Core (lightweight — no engines)

```bash
pip install -e .
```

### Optional extras

| Extra | Purpose |
|---|---|
| `pdf` | PDF page rendering via PyMuPDF |
| `kraken` | Neural layout segmentation and recognition |
| `tesseract` | Baseline OCR via Tesseract |
| `opencv` | Image preprocessing (Sauvola binarization) |
| `docx` | DOCX export with Gentium Plus font |
| `vlm` | Vision-language model (stdlib, no extra deps) |
| `calamari` | GPLv3 Calamari OCR (subprocess-isolated) |
| `server` | FastAPI + RQ server edition |
| `cloud` | FastAPI + Celery + Redis cloud edition |
| `dev` | Testing, linting, type-checking |

**Recommended for all features:**

```bash
pip install -e ".[pdf,kraken,opencv,docx,dev]"
```

---

## Usage

### CLI (headless — agents, CI, batch)

Installing the package registers an `omniocr` command. It never prompts, never
opens a UI, and never reads stdin.

```bash
omniocr doctor --json
```

```bash
omniocr run book.pdf --engine ensemble --script polytonic
```

`--out` is optional. Without it, output lands in `Outputs/<input-name>.txt`
(created automatically) — pass `--out` or `--format` to choose the file or
format explicitly:

```bash
omniocr run book.pdf --out book.docx --engine ensemble --script polytonic
```

Check the environment before a long run — `doctor` exits `3` and names the
problem when an engine, language pack, or model is missing:

```bash
omniocr doctor
```

Cap the work while testing a setup, instead of committing to a whole book —
also the fastest way to check that the engine/language/script combination
actually works before committing to a long run:

```bash
omniocr run book.pdf --max-pages 5 --json
```

**Speed.** `--engine tesseract` is seconds per page. `--engine kraken` and the
default `--engine ensemble` also run Kraken on CPU, which measured **minutes
per page**, not seconds. Size any process timeout accordingly — a short
timeout on the default engine reads as a hang, not slowness. `--engine
tesseract` is the fast path for a first pass or a CI smoke check.

**Contract for unattended callers**

| Aspect | Behaviour |
|---|---|
| stdout | With `--json`, exactly one JSON document. Nothing else — safe to pipe to `jq`. |
| stderr | Per-page progress and all diagnostics, in every mode. |
| exit 0 | Completed with no page failures. **Not** a claim of accuracy — OCR always has some error rate; `"ok": true` means the pipeline ran, not that the text is error-free. |
| exit 1 | Pipeline or export failed. Partial output may still have been written — check `failures` in the JSON. |
| exit 2 | Bad arguments or missing input file. |
| exit 3 | Environment problem: missing engine, language pack, or model. |

Output format comes from the `--out` extension (`.txt`, `.md`, `.docx`, `.pdf`,
`.xml`) and can be overridden with `--format` (adds `page` for PAGE-XML).

Run it without installing:

```bash
python -m omniocr.interfaces.cli doctor
```

### Desktop Edition (Streamlit review UI)

```bash
streamlit run editions/desktop/review_ui.py
```

1. **Upload** a PDF or image
2. **Pipeline runs automatically** — layout segmentation → recognition → reconciliation → post-correction
3. **Review** — side-by-side image and text pane
4. **Suggestions** — each suggestion shown with reason; click Accept to capture ground truth
5. **Polytonic keyboard** — type `a>` for ἀ, `h<` for ἡ, etc.
6. **Export** — Markdown (built-in), DOCX (with `docx` extra)
7. **Ground truth** — accepted corrections saved by line_id with CER comparison

### Server Edition (FastAPI + RQ)

```bash
# Terminal 1 — API
uvicorn editions.server.main:app --host 0.0.0.0 --port 8000

# Terminal 2 — Worker (requires Redis)
rq worker --url redis://localhost:6379 omniocr
```

```
POST /ocr/submit          → 202 { "job_id": "..." }
GET  /ocr/status/{id}     → { "status": "completed" }
GET  /ocr/result/{id}     → (Markdown response)
```

### Cloud Edition (FastAPI + Celery + Redis)

```bash
# All at once
docker compose up

# Or manually:
# Terminal 1 — Redis
docker run -p 6379:6379 redis:7-alpine

# Terminal 2 — FastAPI
uvicorn editions.cloud.main:app --host 0.0.0.0 --port 8000

# Terminal 3 — Worker
celery -A editions.cloud.celery_app worker --loglevel=info
```

### VLM / OpenRouter (opt-in)

Add an LLM-based reviewer for difficult pages:

```bash
export OMNIOCR_ENABLE_VLM=true
export OMNIOCR_VLM_API_KEY=sk-or-v1-...
streamlit run editions/desktop/review_ui.py
```

The VLM engine defaults to `google/gemini-3.5-flash-lite` via OpenRouter with `temperature=0.0` for deterministic output. Configure via:

```python
pipeline = create_ensemble_pipeline(
    tesseract_language="grc+ell+eng",
    kraken_model_path="./models/kraken.mlmodel",
    vlm_api_key="sk-or-v1-...",
    vlm_api_url="https://openrouter.ai/api/v1",   # default
    vlm_model="google/gemini-3.5-flash-lite",       # default
)
```

---

## Pipeline Architecture

```
Upload (PDF/image)
   │
   ▼
INGEST — PyMuPDF page-streaming (lazy generator, large PDFs ✓)
   │
   ▼
PREPROCESS — Grayscale → (optional Sauvola binarization)
   │
   ▼
LAYOUT — Kraken pageseg line segmentation + region classification
   │         (main / apparatus / scholia / running head)
   ▼
SCRIPT ROUTER — Route by script (ancient, polytonic, byzantine, …)
   │
   ▼
RECOGNITION BANK
   ├─ Tesseract (ell, grc — fast baseline + word boxes)
   ├─ Kraken (.mlmodel — accuracy driver for historical print)
   └─ VLM (OpenRouter — opt-in, grounding-guarded)
   │
   ▼
RECONCILE — Confidence-weighted vote across engines
   │
   ▼
POST-CORRECT — Suggest-only (never rewrites source):
   ├─ NFC normalization (Unicode hygiene only)
   ├─ Dangling combining mark detection
   ├─ Ligature expansion (reversible, e.g. ϗ → και)
   ├─ Abbreviation expansion (κ.τ.λ. → καὶ τὰ λοιπά)
   ├─ Diacritic validator (impossible breathing/accent combos)
   └─ Lexicon highlighter (Byzantine + Pontian, suggest-only)
   │
   ▼
EXPORT — TXT / Markdown / ALTO / PAGE-XML / Searchable PDF / DOCX
```

---

## Project Structure

```
OCR/
├── packages/omniocr/              # THE shared core — only place logic lives
│   └── src/omniocr/
│       ├── domain/                 # Pure, no I/O, frozen dataclasses
│       │   ├── models.py           # OCRBlock, OCRLine, DocumentPage, RegionType, …
│       │   ├── result.py           # Ok/Err railway-oriented monad
│       │   └── errors.py           # PipelineError hierarchy
│       ├── ports/                  # Abstract interfaces (hexagon boundary)
│       │   └── interfaces.py       # IOCREngine, ILayoutAnalyzer, IExporter, …
│       ├── application/            # Pipeline orchestration (functional core)
│       │   ├── pipeline.py         # PipelineOrchestrator, SuggestOnlyCorrector
│       │   ├── metrics.py          # CER/WER/regression_gate
│       │   ├── reconcile.py        # Confidence-weighted voting
│       │   └── router.py           # Script→engine routing
│       ├── infrastructure/         # Adapters (engines, exporters, persistence)
│       │   ├── kraken.py           # KrakenEngine + KrakenLayoutAnalyzer
│       │   ├── tesseract.py        # TesseractEngine (hash-verified models)
│       │   ├── vlm.py              # VLMEngine (OpenRouter, grounding-guarded)
│       │   ├── calamari.py         # CalamariEngine (subprocess, GPL-isolated)
│       │   ├── grounding.py        # IoU grounding guard for VLM
│       │   ├── exporters.py        # ALTO, PAGE-XML, Markdown, PDF, DOCX
│       │   ├── ingest.py           # DocumentPageSource (PyMuPDF streaming)
│       │   ├── preprocess.py       # Grayscale + Sauvola binarization
│       │   ├── resilience.py       # Retry, CircuitBreaker, Caching (LRU)
│       │   ├── review.py           # ReviewPage/ReviewDocument models
│       │   ├── training.py         # Kraken fine-training pipeline
│       │   ├── config.py           # Settings with env-backed, validated config
│       │   ├── logging.py          # structlog structured logging
│       │   ├── security.py         # Upload validation (magic bytes, paths)
│       │   ├── events.py           # InMemoryEventBus
│       │   ├── jobs.py             # InMemoryJobStore + SQLiteJobStore
│       │   ├── lexicons.py         # Byzantine + Pontian word lists
│       │   ├── lexicon.py          # SetLexicon adapter
│       │   └── models.py           # SHA-256 model hash verification
│       ├── composition/            # Factory functions (DI wiring)
│       │   └── desktop.py          # create_ensemble_pipeline, etc.
│       └── testing/                # Test-only helpers
│           └── fixtures.py         # Corpus loader
├── editions/
│   ├── desktop/                    # Streamlit review UI
│   ├── server/                     # FastAPI + RQ
│   ├── cloud/                      # FastAPI + Celery + Docker Compose
│   ├── legacy-*/                   # Original editions (pre-migration)
├── tests/
│   ├── corpus/                     # Fixture images + ground truth
│   ├── test_config.py …            # 136 tests across 20 files
│   ├── test_properties.py          # Hypothesis property tests
│   ├── test_contracts.py           # IOCREngine port contract tests
│   ├── test_e2e.py                 # Pipeline E2E smoke tests
│   └── test_regression.py          # CER/WER regression gate
├── scripts/
│   ├── generate_fixtures.py        # Synthetic fixture page generator
│   ├── compute_baselines.py        # CER/WER baseline computation
│   ├── train_kraken.py             # Kraken fine-tune with MLflow
│   └── check_license_isolation.py  # GPLv3 contamination check
├── prototype/                      # Legacy prototype (ocr.py)
└── models/                         # Pinned model artifacts (git-lfs)
```

---

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `OMNIOCR_APP_NAME` | `"omniocr"` | App name |
| `OMNIOCR_DESKTOP_MODE` | `true` | Enable desktop-specific features |
| `OMNIOCR_ENABLE_VLM` | `false` | Enable vision-language model reviewer |
| `OMNIOCR_ENABLE_CALAMARI` | `false` | Enable Calamari OCR (GPLv3) |
| `OMNIOCR_MAX_UPLOAD_BYTES` | `2GB` | Maximum upload size |
| `OMNIOCR_VLM_API_KEY` | — | OpenRouter API key (required for VLM) |

---

## Testing

136 tests across 20 files, 87% coverage.

```bash
# Full suite
python -m pytest

# Specific test suites
python -m pytest tests/test_regression.py -v   # CER/WER regression gate
python -m pytest tests/test_contracts.py -v    # Engine port contracts
python -m pytest tests/test_properties.py -v   # Hypothesis property tests
python -m pytest tests/test_e2e.py -v          # E2E smoke tests

# Linting + type checking
ruff check packages tests
mypy --strict packages/omniocr/src

# CER baselines
python scripts/generate_fixtures.py
python scripts/compute_baselines.py
```

### Test pyramid

| Layer | Count | Coverage |
|---|---|---|
| Unit tests | 74 | Domain, pipeline, post-correction, metrics |
| Property tests | 10 | NFC idempotence, invariants, box arithmetic |
| Contract tests | 24 | IOCREngine: 8 configs × 3 assertions |
| E2E smoke tests | 4 | Ingest → pipeline → export (Markdown, ALTO, PAGE-XML) |
| Regression gate | 8 | CER/WER per fixture across 4 script varieties |

---

## Quality & Security

- **Faithfulness CI-gated** — a PR that mutates source text fails the build
- **License isolation CI** — GPLv3 Calamari imports forbidden in core
- **Upload validation** — magic bytes, size caps, path-traversal guards
- **`mypy --strict`** — enforced in CI on all 36 source files
- **Secrets never logged** — `vlm_api_key` masked via `repr=False`
- **`bandit` + `pip-audit`** — security + dependency audits in CI
- **Structured logging** — `structlog` with per-page timing and event bus

---

## Performance & Concurrency

- **CPU-only** — Tesseract + Kraken both run on CPU. VLM calls the cloud.
- **Streaming ingest** — PyMuPDF yields pages lazily; 2000-page books never land in memory at once.
- **Per-page isolation** — one bad page never kills a job. Failed pages are retained for review.
- **Checkpoint/resume** — SQLite-backed job store persists progress; jobs resume after interruption.
- **Railway-oriented errors** — `Result[T, E]` threads through the pipeline; exceptions reserved for programmer errors.
- **Engine deduplication** — each engine called once per page; blocks assigned to segments by bounding-box overlap.

---

## Extras

### Kraken Training Pipeline

Fine-tune on specific Byzantine printed typefaces:

```bash
pip install kraken mlflow
python scripts/train_kraken.py \
    --train-dir ./training_data \
    --base-model ./models/kraken_base.mlmodel \
    --output-model ./models/finetuned.mlmodel \
    --epochs 10
```

Tracks CER improvement in MLflow.

### Export Formats

| Format | Coverage |
|---|---|
| TXT | Plain text, one line per page |
| Markdown | Page headings, Unicode |
| ALTO XML | Geometry, confidence, provenance, region type, reading order |
| PAGE-XML | Coordinates, confidence, engine provenance, region type |
| Searchable PDF | Overlays original image; auto-detects system font for Greek |
| DOCX | Gentium Plus font with Byzantine glyph coverage |

### Fixture Corpus

4 synthetic fixture pages covering modern, polytonic, ancient, and Byzantine Greek. Committed baselines with CI regression gate ensure no CER regression on any variety.

---

## License

Core library: MIT. Calamari OCR: GPLv3 (subprocess-isolated, never imported into core).

---

## Citation

If you use OmniOCR in academic work:

```bibtex
@software{omniocr2026,
  author = {OmniOCR Contributors},
  title = {OmniOCR: Printable Greek OCR Suite},
  year = {2026},
  url = {https://github.com/your-org/omniocr}
}
```
