"""Cloud Edition composition root for OmniOCR.

Multi-tenant pipeline with Celery-backed async processing,
upload validation, and full engine ensemble.

This edition does not wire adapters itself. It delegates to the core
composition root and supplies only what is genuinely cloud-specific — its
settings. Re-wiring the pipeline here would silently drop the
invariants ``create_ensemble_pipeline`` enforces (resilience decorators, the
VLM grounding guard, script lexicons, promoted-model routing).
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.composition import create_ensemble_pipeline
from omniocr.domain.models import Script
from omniocr.infrastructure.config import Settings


def create_cloud_pipeline(
    settings: Settings | None = None,
) -> PipelineOrchestrator:
    """Build a multi-tenant cloud pipeline with the full engine ensemble.

    Everything — engines, resilience wrapping, routing, post-correction —
    comes from the shared composition root.
    """
    cfg = settings or Settings.from_env()
    return create_ensemble_pipeline(
        tesseract_language=cfg.tesseract_language,
        kraken_model_path=cfg.kraken_model_path,
        script=Script.POLYTONIC,
        vlm_api_key=cfg.vlm_api_key if cfg.enable_vlm else None,
        unlimited_ocr_url=cfg.unlimited_ocr_url if cfg.enable_vlm else None,
    )
