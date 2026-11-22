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
| `OMNIOCR_MAX_WORKERS` | `1` | Pages a server or cloud worker recognizes at once (1 = one after another). The CLI uses `--workers` instead |
| `OMNIOCR_CACHE_TTL` | — | Cache TTL in seconds for CachingEngine (unset = no TTL) |
| `OMNIOCR_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for the Cloud broker |

## Performance Tuning

### Parallel page processing

Set `max_workers` when constructing the pipeline to enable ThreadPoolExecutor-based
parallelism. Each page is processed in a separate thread; results are assembled in
page-number order:

```python
pipeline = PipelineOrchestrator(max_workers=4, ...)
result = pipeline.run(pdf_bytes)
```

Start with `max_workers=2` and scale up to the number of CPU cores. The default
(`None`) preserves the original synchronous, single-threaded behavior.

The server and cloud editions build the pipeline for you: set `OMNIOCR_MAX_WORKERS` on the
worker (the compose files pass it through, default 1). Every page in flight holds its image and
runs the engines, so memory and CPU grow with it. Measured on 7 corpus pages on a 24-thread
CPU: 52 s at 1 worker, 33 s at 4, with byte-identical text. Check `omniocr_page_duration_seconds`
and the container's memory before raising it on a long book.

### TTL cache eviction

Wrap engines with `CachingEngine(engine, ttl=3600)` to prevent cross-document cache
leakage in long-running worker processes. Entries expire after `ttl` seconds:

```python
from omniocr.infrastructure.resilience import CachingEngine
cached = CachingEngine(my_engine, max_size=128, ttl=3600)
```

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

### Metrics (server and cloud)

`GET /metrics` on the API serves Prometheus text. It needs a valid API key, so scrape with a
bearer token (`authorization: {type: Bearer, credentials: <key>}`), and the scraper's `Host`
must be in `OMNIOCR_ALLOWED_HOSTS`. There are no tenant labels.

| Metric | Written by | Labels |
|---|---|---|
| `omniocr_http_requests_total` | API | `method`, `route` (template, never the URL), `status` |
| `omniocr_jobs_submitted_total` | API | none |
| `omniocr_pages_total` | worker | `outcome` = `completed` or `failed` |
| `omniocr_page_duration_seconds` | worker | histogram |
| `omniocr_page_failures_total` | worker | `error_type` (for example `EmptyPage`) |
| `omniocr_jobs_total` | worker | `outcome` = `done` or `failed` |
| `omniocr_job_duration_seconds` | worker | histogram |

The API and the workers are separate containers. The compose files mount one volume
(`omniocr-metrics`) at `/metrics` in both and set `PROMETHEUS_MULTIPROC_DIR=/metrics`; every
process writes its counters there and a scrape adds them up. Without that variable, `/metrics`
shows only the API's own numbers. Counts survive restarts because the files stay on the volume;
`docker compose down -v` resets them. Each worker process leaves a small file set behind, and an
RQ work horse is one process per job, so the directory grows with the number of jobs.
