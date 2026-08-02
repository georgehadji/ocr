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
| `OMNIOCR_MAX_WORKERS` | — | Number of threads for parallel page processing (unset = sequential) |
| `OMNIOCR_CACHE_TTL` | — | Cache TTL in seconds for CachingEngine (unset = no TTL) |
| `OMNIOCR_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for Cloud job store |

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

### TTL cache eviction

Wrap engines with `CachingEngine(engine, ttl=3600)` to prevent cross-document cache
leakage in long-running worker processes. Entries expire after `ttl` seconds:

```python
from omniocr.infrastructure.resilience import CachingEngine
cached = CachingEngine(my_engine, max_size=128, ttl=3600)
```

### Redis job store (Cloud edition)

The Cloud composition root wires `RedisJobStore` for durable checkpoint persistence
across worker restarts. Configure via `OMNIOCR_REDIS_URL`. If Redis is unreachable,
the Cloud composition falls back to a `SQLiteJobStore` (`omniocr_cloud_jobs.db`),
so a standalone Cloud deployment still persists checkpoints:

```python
from omniocr.infrastructure.jobs import RedisJobStore
job_store = RedisJobStore(redis_url="redis://my-redis:6379/0")
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
