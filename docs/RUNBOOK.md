# OmniOCR Operator Runbook

## Installation

```bash
pip install -e ".[pdf,kraken,opencv,docx,dev]"
```

### For cloud/server:

```bash
pip install -e ".[cloud]"   # or .[server]
```

## Starting

### Desktop (Streamlit review UI)

```bash
streamlit run editions/desktop/review_ui.py
```
URL: http://localhost:8501

### Server (FastAPI + RQ)

```bash
uvicorn editions.server.main:app --host 0.0.0.0 --port 8000
rq worker --url redis://localhost:6379 omniocr
```
API: http://localhost:8000

### Cloud (FastAPI + Celery + Redis)

```bash
docker compose -f editions/cloud/docker-compose.yml up
uvicorn editions.cloud.main:app --host 0.0.0.0 --port 8000
celery -A editions.cloud.celery_app worker --loglevel=info
```
API: http://localhost:8000/docs

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OMNIOCR_MAX_UPLOAD_BYTES` | 2 GB | Max upload size |
| `OMNIOCR_ENABLE_VLM` | `false` | Enable VLM reviewer |
| `OMNIOCR_VLM_API_KEY` | — | OpenRouter API key |
| `OMNIOCR_APP_NAME` | `"omniocr"` | App identifier for logs |

## Common Issues

| Symptom | Likely Cause | Fix |
|---|---|---|
| `No module named 'kraken'` | Kraken not installed | `pip install -e ".[kraken]"` |
| `Image is not bi-level` | Wrong PIL mode | Fixed in code — reinstall if using old version |
| `Kraken layout segmentation failed` | Kraken not installed | `pip install -e ".[kraken]"` |
| Upload > 200 MB | Streamlit default limit | Already configured for 2 GB — check `.streamlit/config.toml` |
| `VLM extraction failed` | Missing or invalid API key | Check `OMNIOCR_VLM_API_KEY` |
| Searchable PDF fails | Missing font | Auto-detects system font — install Arial or Gentium Plus |

## Monitoring

- Pipeline logs: `structlog` with `pipeline_start`, `page_completed`, `page_failed`
- VLM: no per-request logging yet — use network proxy
- Errors: all wrapped in `Result` types — no silent failures
- Coverage: `python -m pytest --cov=packages/omniocr`
