<h1 align="center">OmniOCR</h1>

<p align="center">
  <strong>Printed Greek OCR — Modern, polytonic, Ancient, Byzantine, Pontian</strong><br>
  Faithful (diplomatic) transcription for scholarly, liturgical, and historical texts.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-blue" alt="Python 3.11 / 3.12">
  <img src="https://img.shields.io/badge/typing-mypy%20--strict-blue" alt="mypy --strict">
  <img src="https://img.shields.io/badge/coverage-%E2%89%A580%25%20CI--enforced-brightgreen" alt="Coverage gate 80%">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

---

OmniOCR converts scanned Greek print into editable, searchable text. It targets the
varieties general-purpose OCR handles worst — polytonic orthography, critical editions
with apparatus, Byzantine typefaces and ligatures — and exports to DOCX, Markdown,
searchable PDF, TXT, ALTO-XML, and PAGE-XML.

It runs **CPU-only** by default, uses a GPU automatically when one is present, and is
built around one non-negotiable rule.

### Faithfulness over fluency

For scholarly Greek a plausible reading is worse than a flagged uncertain one. No stage
of the pipeline silently alters recognized text.

- **Box-grounded engines** (Tesseract, Kraken) are the source of truth. Every character
  is auditable back to a region of the page image.
- **Post-correction suggests, never rewrites.** Lexicons, diacritic validation, and
  hyphen joins emit `Suggestion` objects with a reason. A human accepts or rejects.
- **VLM output is opt-in** and always reconciled against box-grounded output. Divergence
  raises a flag; it never overwrites.
- **Archival exports are verbatim.** ALTO and PAGE-XML always carry the raw recognized
  lines, whatever the rendering exports do.

---

## Install

Requires Python 3.11+ and [Tesseract](https://github.com/tesseract-ocr/tesseract) on
`PATH` with the `grc` and `ell` language packs.

```bash
pip install -e ".[pdf,kraken,opencv,docx,dev]"
```

The core package has **zero required dependencies** — every engine and format is an
extra, so a minimal install stays small.

| Extra | Enables |
|---|---|
| `pdf` | PDF page rendering and streaming ingest (PyMuPDF) |
| `tesseract` | Tesseract recognition and word boxes |
| `kraken` | Kraken segmentation and recognition — the accuracy driver |
| `opencv` | Sauvola binarization and preprocessing |
| `docx` | DOCX export |
| `vlm` | Vision-language second opinion (stdlib only, no new deps) |
| `calamari` | Calamari voting booster — **GPLv3**, subprocess-isolated |
| `training` | Kraken fine-tuning with MLflow tracking |
| `server` / `cloud` | Server and Cloud edition runtimes |
| `dev` | pytest, ruff, mypy, bandit, pip-audit |

### GPU, and the torch/torchvision trap

The `kraken` extra pulls in **torch** and **torchvision**, and the two must
come from the *same* wheel index. Installing one alone is the most common way
to break this project:

```bash
# WRONG — reinstalls torch only, strands torchvision on a different ABI
pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu124
```

Each CUDA index carries its own version ceiling — cu124 tops out at torch 2.6 /
torchvision 0.21 — so that command can silently *downgrade* torch and leave a
torchvision built for a newer one. Kraken then fails on every page with
`operator torchvision::nms does not exist`, which names neither the cause nor
the fix. Install all of them together instead:

```bash
# CPU (default, always works)
pip install --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# CUDA — pick ONE index and pin all three to it
pip install --force-reinstall torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
```

A GPU is never required. It is used automatically when present and is worth
having: Kraken recognition is the pipeline's bottleneck at roughly 60–90 s per
page on CPU. Check your card's compute capability is in `torch.cuda.get_arch_list()`
before assuming a CUDA build supports it — recent builds have dropped older
architectures.

Verify the environment before committing to a long run:

```bash
omniocr doctor
```

`doctor` reports the torch/torchvision pairing and exits non-zero if it is
broken, so this class of failure surfaces in a second rather than an hour in.

`doctor` exits `3` and names the missing engine, language pack, or model.

---

## Quick start

```bash
omniocr run book.pdf --out book.docx --engine ensemble --script polytonic --structure
```

Without `--out`, output lands in `Outputs/<input-name>.txt`. Format is inferred from the
extension and can be forced with `--format`.

Always smoke-test the engine/script/language combination before a full book:

```bash
omniocr run book.pdf --max-pages 5 --json
```

### CLI reference

| Flag | Default | Purpose |
|---|---|---|
| `--engine` | `ensemble` | `tesseract` (fast) · `kraken` · `ensemble` |
| `--script` | `polytonic` | `modern` `polytonic` `ancient` `byzantine` `pontian` `mixed` `unknown` |
| `--format` | from `--out` | `txt` `md` `docx` `pdf` `alto` `page` |
| `--lang` | `grc` | Tesseract language pack |
| `--model` | manifest default | Kraken `.mlmodel` path |
| `--structure` | off | Reconstruct paragraphs, join hyphens, mark running heads |
| `--workers N` | `1` | Process N pages concurrently |
| `--max-pages N` | all | Stop after N pages |
| `--json` | off | One JSON document on stdout |

### Contract for unattended callers

| Aspect | Behaviour |
|---|---|
| stdout | With `--json`, exactly one JSON document — safe to pipe to `jq`. |
| stderr | All progress and diagnostics, in every mode. |
| exit `0` | Ran with no page failures. **Not a claim of accuracy** — OCR always has an error rate. |
| exit `1` | Pipeline or export failed. Partial output may exist; check `failures`. |
| exit `2` | Bad arguments or missing input. |
| exit `3` | Environment problem — missing engine, language pack, or model. |

---

## Accuracy: the model matters more than the engine

Measured on real target material, the three bundled Kraken models span **0.038–0.264
CER** while Tesseract sits at **0.056**. The wrong Kraken model is far worse than
Tesseract; the right one is substantially better.

**Never select a model by filename order.** The default is declared in
`models/manifest.json`. See [docs/ENGINE_ACCURACY.md](docs/ENGINE_ACCURACY.md) for the
per-variety numbers.

Tesseract remains the fast baseline and the source of word boxes for searchable PDF.

---

## Performance

`--engine tesseract` runs in **seconds per page**. `kraken` and the default `ensemble`
run Kraken, which measures in **minutes per page on CPU** — size process timeouts
accordingly, or a slow run reads as a hang.

`--workers N` processes pages concurrently. Note that Kraken recognition is serialized
per engine instance (its `rpred` path has no upstream thread-safety guarantee), so today
the flag mainly benefits Tesseract; see
[ADR-003](docs/adr/003-threadpoolexecutor-parallelism.md).

Ingest streams pages lazily, so a 2000-page book never lands in memory at once. Page
failures are isolated — one bad page never kills a job — and SQLite-backed checkpointing
lets an interrupted run resume.

---

## Pipeline

```
INGEST         PyMuPDF page streaming
     ↓
PREPROCESS     Grayscale → optional Sauvola binarization
     ↓
LAYOUT         Kraken pageseg, falling back to Tesseract when it returns zero lines
     ↓
ROUTE          Script → engine selection
     ↓
RECOGNIZE      Tesseract · Kraken · Calamari (opt-in) · VLM (opt-in)
     ↓
RECONCILE      Script-aware / confidence-weighted vote
     ↓
POST-CORRECT   NFC · diacritic validation · ligature and abbreviation expansion ·
               lexicon highlighting            ← all suggest-only
     ↓
ASSEMBLE       Paragraphs, hyphen joins, running heads, roles   (--structure)
     ↓
EXPORT         TXT · Markdown · DOCX · searchable PDF · ALTO · PAGE-XML
```

Document structure recovery is opt-in and additive: paragraphs are a **view** over
untouched lines, every hyphen join records the exact character removed so it can be
reversed, and pages detected as multi-column are skipped rather than assembled wrongly.
See [docs/DOCUMENT_STRUCTURE.md](docs/DOCUMENT_STRUCTURE.md).

---

## Editions

One shared core (`packages/omniocr`); editions differ only in their composition root.

| Edition | Stack | Use for |
|---|---|---|
| **Desktop** | Streamlit | Single-scholar review and correction |
| **Server** | FastAPI + RQ + Redis | Headless batch processing |
| **Cloud** | FastAPI + Celery + Redis + Docker | Multi-tenant deployment |

```bash
# Desktop — review UI with polytonic keyboard and suggestion accept/reject
streamlit run editions/desktop/review_ui.py

# Server — API plus worker
uvicorn editions.server.main:app --port 8000
rq worker --url redis://localhost:6379 omniocr

# Cloud
docker compose -f editions/cloud/docker-compose.yml up
```

---

## Architecture

Clean hexagonal design; dependencies point inward.

```
interfaces → infrastructure → application → domain
```

- **`domain/`** — frozen dataclasses, `Result[T, E]`, zero I/O.
- **`ports/`** — `Protocol` interfaces (`IOCREngine`, `ILayoutAnalyzer`, `IExporter`, …).
- **`application/`** — orchestration, reconciliation, routing, structure assembly.
- **`infrastructure/`** — engine, export, and persistence adapters.
- **`composition/`** — dependency wiring per edition.

Two rules are CI-enforced rather than documented and hoped for:
`scripts/check_layering.py` fails the build on an inward dependency violation, and
`scripts/check_license_isolation.py` fails it on a GPL import reaching the core.

Full design of record: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Development

401 tests across 56 files. Every gate below runs in CI on Ubuntu and Windows,
Python 3.11 and 3.12.

```bash
python -m pytest                                   # full suite
python -m pytest --cov=packages/omniocr/src/omniocr --cov-fail-under=80
ruff check packages tests editions scripts
ruff format --check packages tests editions scripts
mypy --strict --ignore-missing-imports packages/omniocr/src
python scripts/check_license_isolation.py          # GPL containment
python scripts/check_layering.py                   # hexagonal boundaries
python -m pytest tests/test_regression.py          # CER/WER regression gate
python -m bandit -r packages/omniocr/src/omniocr -s B101
python -m pip_audit
```

Slow tests (Kraken inference, training) are marked and excluded by default:
`pytest -m slow` to include them.

The suite covers unit, property-based (Hypothesis), port-contract, end-to-end, and CER
regression layers. Faithfulness is machine-checked: a change that mutates recognized
text fails the build.

---

## Licensing

**OmniOCR core is MIT** (see [LICENSE](LICENSE)) and contains no copyleft code.

Two optional components are **GPLv3** and are deliberately kept at arm's length —
invoked as separate processes over stdin/stdout, never imported, never linked:

| Component | License | Status |
|---|---|---|
| Calamari OCR | GPLv3 | Optional `calamari` extra, subprocess-isolated |
| Polytone (polytonic reconstruction) | GPL-3.0 | Evaluated, not integrated — see [docs/POLYTONIC_RECONSTRUCTION.md](docs/POLYTONIC_RECONSTRUCTION.md) |

`scripts/check_license_isolation.py` fails CI if a GPL module is imported into the core,
so the boundary cannot erode silently.

> **If you redistribute OmniOCR commercially**, note that *shipping* a GPL component
> alongside it is a different question from *calling* one that the user installed
> themselves. Not bundling them is the unambiguous path. Get the distribution model
> reviewed by counsel before shipping — this file is not legal advice.

---

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design of record — read before non-trivial work |
| [ENGINE_ACCURACY.md](docs/ENGINE_ACCURACY.md) | Measured CER per engine and model |
| [DOCUMENT_STRUCTURE.md](docs/DOCUMENT_STRUCTURE.md) | Paragraphs, hyphens, running heads |
| [POLYTONIC_RECONSTRUCTION.md](docs/POLYTONIC_RECONSTRUCTION.md) | Diacritic-check feasibility study |
| [VLM_COST_OPTIMIZATION.md](docs/VLM_COST_OPTIMIZATION.md) | Tuning VLM spend |
| [RUNBOOK.md](docs/RUNBOOK.md) | Operational procedures |
| [adr/](docs/adr/) | Architecture decision records |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) | Process and disclosure |
