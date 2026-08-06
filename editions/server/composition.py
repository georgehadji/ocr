"""Server Edition composition root for OmniOCR.

Wires the pipeline with RQ-backed async job processing,
API key validation, upload validation, and configurable engines.
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.composition import create_ensemble_pipeline
from omniocr.domain.models import Script, TenantContext
from omniocr.domain.result import Err
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.jobs import SQLiteJobStore
from omniocr.infrastructure.security import validate_upload


def create_server_pipeline(
    settings: Settings | None = None,
) -> PipelineOrchestrator:
    """Build a server-grade pipeline with retry resilience and persistence.

    Queuing is handled externally by RQ; the pipeline runs synchronously
    inside the worker process. Only the job store is server-specific — engines,
    routing and post-correction come from the shared composition root, so this
    edition cannot drift from the invariants the core enforces.
    """
    cfg = settings or Settings.from_env()
    return create_ensemble_pipeline(
        tesseract_language=cfg.tesseract_language,
        kraken_model_path=cfg.kraken_model_path,
        script=Script.POLYTONIC,
        job_store=SQLiteJobStore("omniocr_jobs.db"),
        vlm_api_key=cfg.vlm_api_key if cfg.enable_vlm else None,
    )


def run_ocr_job(job_data: bytes) -> bytes:
    """Called by RQ worker: validate, run pipeline, return result bytes.

    Takes no settings argument: the worker's own environment is the authority
    on how it should run. The previous ``settings_json`` parameter was never
    read — both call sites passed a live ``Settings`` dataclass into a
    parameter annotated ``str``, and the body called ``Settings.from_env()``
    regardless.

    Guards narrow with ``isinstance(..., Err)`` rather than ``is_err()``,
    which returns a plain ``bool`` and type-checks nothing downstream.
    """
    settings = Settings.from_env()
    max_bytes = settings.max_upload_bytes
    upload_result = validate_upload(job_data, "upload.pdf", max_bytes)
    if isinstance(upload_result, Err):
        raise ValueError(str(upload_result.error))

    pipeline = create_server_pipeline(settings)
    ctx = TenantContext(
        organization_id="server",
        user_id="worker",
        subscription_tier="server",
    )
    result = pipeline.run(upload_result.value, ctx)
    if isinstance(result, Err):
        raise RuntimeError(str(result.error))
    export_result = pipeline.export(result.value, ctx)
    if isinstance(export_result, Err):
        raise RuntimeError(str(export_result.error))
    return export_result.value
