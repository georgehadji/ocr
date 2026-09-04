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

- **`ocr.py`** (root) is the only fully working code — a Streamlit prototype: Tesseract
  ensemble (`grc+ell+eng`), 2-pass PSM voting, OpenCV UMat preprocessing, DOCX/PDF export.
- The three **Editions** are mostly **clean-architecture scaffolding** — `app_core` interfaces
  and domain models are defined, but most engines are **stubs** (e.g. Cloud
  `app_core/engines.py` returns `[]`). Desktop Edition has the most real code (a working
  Tesseract strategy + MLflow exporter). Treat the Editions as the target structure to
  complete, not finished software.
- No automated test suite exists yet. When building out `omniocr_core`, add `pytest` (unit
  tests for domain/post-correction, integration tests for engines against fixture pages).

## Commands

Tesseract must be installed and on `PATH` (with `ell` and `grc` language data). Each edition
has its own `requirements.txt`.

- **Root prototype**: `streamlit run ocr.py`
- **Cloud Edition** (FastAPI + Celery + Redis + Streamlit):
  - UI: `cd "Cloud Edition/omniocr_master_edition" && streamlit run frontend_ui/app.py`
  - API: `cd "Cloud Edition/omniocr_master_edition" && uvicorn backend_worker.worker_api:app --host 0.0.0.0 --port 8000`
  - Orchestration: `Cloud Edition/omniocr_master_edition/orchestration/docker-compose.yml`
- **Desktop Trainer Edition** (fine-tuning + MLflow):
  `cd "Desktop Edition/omniocr_trainer_edition" && streamlit run trainer_ui/app.py`
- **Server Standalone Edition** (FastAPI + RQ + static UI):
  - API: `cd "Server Standalone Edition/omniocr_server_edition" && uvicorn backend_api.main:app --host 0.0.0.0 --port 8000`
  - Worker: `cd "Server Standalone Edition/omniocr_server_edition" && rq worker --url redis://localhost:6379`

## Architecture in one screen

Clean/hexagonal architecture in `app_core`; dependencies point inward
(`interfaces → infrastructure → application → domain`). One shared core, three editions that
differ only in their **composition root** (which engines/queue/UI get wired).

Pipeline: **ingest** (PyMuPDF page-streaming for large PDFs) → **preprocess** (OpenCV) →
**layout + line segmentation** (Kraken/Paddle) → **script detect & route** → **recognition
bank** (Tesseract + Kraken; VLM opt-in via cloud API) → **reconcile/vote** →
**faithful post-correction** → **human review** → **export**.

The five repos in `Github/` are engine sources: `tesseract` (fast baseline), `calamari`
(opt-in voting booster — **GPLv3/TensorFlow, subprocess-isolate it**), `PaddleOCR`/`EasyOCR`
(layout/detection), `Unlimited-OCR` (VLM — reference for a future GPU edition; not a CPU engine).
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

## Python conventions

Follow PEP 8, `black` + `isort` + `ruff`, type annotations on all signatures, frozen
dataclasses for domain models. See `docs/ARCHITECTURE.md` for the full build order.
