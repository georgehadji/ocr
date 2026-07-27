"""Server Edition composition root for OmniOCR.

Wires the pipeline with RQ-backed async job processing,
API key validation, upload validation, and configurable engines.
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import SQLiteJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.infrastructure.security import validate_upload
from omniocr.infrastructure.config import Settings
from omniocr.infrastructure.tesseract import TesseractEngine


def create_server_pipeline(
    settings: Settings | None = None,
) -> PipelineOrchestrator:
    """Build a server-grade pipeline with retry resilience and persistence.

    Queuing is handled externally by RQ; the pipeline runs synchronously
    inside the worker process.
    """
    cfg = settings or Settings()

    tesseract = RetryingEngine(
        TesseractEngine(
            cfg.app_name,
            model_path=None,  # server deployments should pin a model path
        )
    )
    kraken = RetryingEngine(KrakenEngine(""))
    router = ScriptRouter(
        by_script={
            Script.ANCIENT: (kraken, tesseract),
            Script.BYZANTINE: (kraken, tesseract),
            Script.POLYTONIC: (kraken, tesseract),
        },
        default=(tesseract,),
    )

    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=KrakenLayoutAnalyzer(),
        router=router,
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=MarkdownExporter(),
        job_store=SQLiteJobStore("omniocr_jobs.db"),
    )


def run_ocr_job(job_data: bytes, settings_json: str) -> bytes:
    """Called by RQ worker: validate, run pipeline, return result bytes."""
    settings = Settings.from_env()
    max_bytes = settings.max_upload_bytes
    upload_result = validate_upload(job_data, "upload.pdf", max_bytes)
    if upload_result.is_err():
        raise ValueError(str(upload_result.error))

    pipeline = create_server_pipeline(settings)
    ctx = TenantContext(
        organization_id="server",
        user_id="worker",
        subscription_tier="server",
    )
    result = pipeline.run(upload_result.value, ctx)
    if result.is_err():
        raise RuntimeError(str(result.error))
    export_result = pipeline.export(result.value, ctx)
    if export_result.is_err():
        raise RuntimeError(str(export_result.error))
    return export_result.value
