# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**OmniOCR** — an OCR suite for **printed Greek in every variety**: modern monotonic,
polytonic, Ancient Greek (critical editions), Byzantine printed typefaces/ligatures, and
Pontian. It ingests large PDFs and images and exports DOCX, Markdown, searchable PDF, TXT,
and ALTO/PAGE-XML.

**The full design of record is [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Read it before
non-trivial work.** Locked scope decisions: **CPU-capable / GPU-opportunistic** (never
*requires* a GPU; uses one automatically via `infrastructure/device.py` when present,
otherwise page-level parallelism), **printed material**, **faithful
(diplomatic) transcription**, **Desktop edition primary**.

## Current state (important)

- **`packages/omniocr`** is the shared core and the working product: the `omniocr` CLI
  (`interfaces/cli.py`: `doctor`, `run`, `eval`), real Tesseract, Kraken, Calamari, VLM and
  Unlimited-OCR adapters in `infrastructure/`, and the composition roots in `composition/`.
- **`editions/{desktop,server,cloud}`** are thin composition roots over that core: a
  Streamlit review UI (`desktop/review_ui.py`), FastAPI + RQ (`server/`), and FastAPI +
  Celery + Docker (`cloud/`). Job state lives in Redis (server) or the Celery result
  backend (cloud), so either API can run with any number of uvicorn workers.
- **`prototype/ocr.py`** is the original Streamlit prototype (Tesseract `grc+ell+eng`, 2-pass
  PSM voting). It is kept for reference; new work goes in `packages/omniocr`.
- The `pytest` suite lives in `tests/` (unit tests for domain/post-correction, integration
  tests for engines against the fixture pages in `tests/corpus/`). Run `pytest` from the repo
  root.

## Commands

Tesseract must be installed and on `PATH` (with `ell` and `grc` language data). Install the
core with `pip install -e ".[dev,docx]"` plus the extras you need (`kraken`, `pdf`, `server`,
`cloud`, `training`; see `pyproject.toml`). Run everything from the repo root.

- **CLI**: `omniocr doctor` (what's missing), `omniocr run book.pdf --out book.docx`,
  `omniocr eval --split test`
- **Desktop** (review UI): `streamlit run editions/desktop/review_ui.py`
- **Server**: `uvicorn editions.server.main:app --port 8000` and
  `rq worker --url redis://localhost:6379 omniocr`
- **Cloud**: `docker compose -f editions/cloud/docker-compose.yml up`
- **CI gates**: see `CONTRIBUTING.md` (pytest with 80% coverage, ruff, mypy strict,
  `scripts/check_layering.py`, `scripts/check_license_isolation.py`)

## Architecture in one screen

Clean/hexagonal architecture in `packages/omniocr/src/omniocr`; dependencies point inward
(`interfaces → infrastructure → application → domain`). One shared core, three editions that
differ only in their **composition root** (which engines/queue/UI get wired).

Pipeline: **ingest** (PyMuPDF page-streaming for large PDFs) → **preprocess** (OpenCV) →
**layout + line segmentation** (Kraken) → **route by `--script`** → **recognition
bank** (Tesseract + Kraken; VLM opt-in via cloud API) → **reconcile/vote** →
**faithful post-correction** → **human review** → **export**.

The five archives in `Github/` are engine sources: `tesseract` (fast baseline), `calamari`
(opt-in voting booster via `--calamari-model` — **GPLv3/TensorFlow, subprocess-isolate it**;
it reads lines, so it is handed the pipeline's own line segmenter), `PaddleOCR`/`EasyOCR`
(layout/detection), `Unlimited-OCR` (VLM — not a CPU engine; served separately and wired as an
opt-in voter via `--unlimited-ocr-url`, see `infrastructure/unlimited_ocr.py`). Both VLM
voters are wrapped in `GroundedEngine`, which drops blocks over regions Tesseract read as empty.
Kraken — the default recognizer — comes from PyPI, not a repo here.

## Rules specific to this codebase

1. **Faithfulness over fluency.** No pipeline stage may silently alter recognized text.
   Post-correction, lexicons, and diacritic checks **suggest/highlight**; the human decides.
   The VLM is opt-in and always reconciled against box-grounded (Tesseract/Kraken) output —
   never the unaudited source. Rationale in ARCHITECTURE.md §1.
2. **Kraken with the right model is the accuracy driver** for polytonic/ancient/Byzantine
   print; Tesseract is the fast baseline and the source of word boxes for searchable PDFs.
   Don't rely on Tesseract alone for the harder varieties. **The model matters more than the
   engine**: measured on real target material, the three bundled Kraken models span
   0.038–0.264 CER while Tesseract sits at 0.056 — so the wrong Kraken model is far worse
   than Tesseract, and the right one is better. Never select a model by filename order; the
   default is declared in `models/manifest.json`. See `docs/ENGINE_ACCURACY.md`.
3. **Pontian is handled in post-correction, not recognition** — recognition is Greek-script;
   dialect coverage comes from a custom Pontian lexicon (suggest-only).
4. **No engine/OpenCV/DB calls in UI or API controllers.** Map to `domain` dataclasses; inject
   engines through `interfaces` ports.
5. **Normalize all output** with `unicodedata.normalize('NFC', …)` — as Unicode hygiene only,
   never an orthographic change.
6. **Offload blocking OCR** off the request loop (`asyncio.to_thread` / executors / the
   edition's Celery or RQ queue).
7. **Strict typing** — avoid `Any`/`cast` suppression; use explicit type guards.
8. **When the documentation and existing code conflict, follow the code's conventions, and in
   your final reply, list all the inconsistencies you found.**

## Python conventions

Follow PEP 8, `black` + `isort` + `ruff`, type annotations on all signatures, frozen
dataclasses for domain models. See `docs/ARCHITECTURE.md` for the full build order.
