# OmniOCR Instruction Manual

OmniOCR is an OCR suite for printed Greek in every variety (modern, polytonic, Ancient,
Byzantine, Pontian). The design of record is [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
the agent rules are in [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md). Where this file and
the code disagree, follow the code.

## Layout

- **`packages/omniocr/src/omniocr`**: the shared core, in clean-architecture layers —
  `domain` (frozen dataclasses, `Result`), `ports` and `application` (pipeline, routing,
  reconciliation, post-correction), `infrastructure` (Tesseract, Kraken, Calamari, VLM and
  Unlimited-OCR adapters, exporters, storage), `composition` (wiring), `interfaces` (the
  `omniocr` CLI).
- **`editions/desktop`**: Streamlit review UI (`review_ui.py`).
- **`editions/server`**: FastAPI + RQ + Redis.
- **`editions/cloud`**: FastAPI + Celery + Redis + Docker.
- **`prototype/ocr.py`**: the original Streamlit prototype, kept for reference.
- **`tests/`**: the pytest suite; fixture pages are in `tests/corpus/`.

## Commands

Tesseract must be on `PATH` with `ell` and `grc` language data. Run from the repo root.

```bash
pip install -e ".[dev,docx]"
omniocr doctor
omniocr run book.pdf --out book.docx
streamlit run editions/desktop/review_ui.py
uvicorn editions.server.main:app --port 8000
rq worker --url redis://localhost:6379 omniocr
docker compose -f editions/cloud/docker-compose.yml up
```

CI gates (pytest with coverage, ruff, mypy strict, layering and license checks) are listed
in [CONTRIBUTING.md](CONTRIBUTING.md).

## Conventions

1. **Faithfulness:** no stage silently alters recognized text; post-correction only suggests.
2. **Dependency direction:** no engine, OpenCV or DB calls in UI or API controllers. Editions
   compose through `omniocr.composition`; `scripts/check_layering.py` enforces it.
3. **Blocking OCR** goes off the request loop (`asyncio.to_thread`, executors, RQ or Celery).
4. **Device:** use `infrastructure/device.py`; never require a GPU.
5. **NFC:** normalize output with `unicodedata.normalize('NFC', ...)`.
6. **Strict typing:** no `Any`/`cast` suppression; use explicit type guards.
7. **Calamari (GPLv3)** stays subprocess-isolated; `scripts/check_license_isolation.py`
   enforces it.
