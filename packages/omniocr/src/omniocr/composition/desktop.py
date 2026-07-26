from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator, SuggestOnlyCorrector
from omniocr.infrastructure.tesseract import TesseractEngine
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import ConfidenceWeightedReconciler
from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import InMemoryJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.ports.interfaces import IExporter, IJobStore

# VLM and Calamari are optional extras; import failures gracefully disable them.
try:
    from omniocr.infrastructure.calamari import CalamariEngine  # noqa: F811

    _CALAMARI_AVAILABLE = True
except ImportError:
    _CALAMARI_AVAILABLE = False

try:
    from omniocr.infrastructure.vlm import VLMEngine  # noqa: F811

    _VLM_AVAILABLE = True
except ImportError:
    _VLM_AVAILABLE = False


def create_desktop_pipeline(
    script: Script = Script.UNKNOWN,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        layout_analyzer=KrakenLayoutAnalyzer(script),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


def create_tesseract_pipeline(
    language: str = "eng",
    script: Script = Script.MODERN,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
) -> PipelineOrchestrator:
    """Build the desktop pipeline with the optional Tesseract engine enabled."""
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=KrakenLayoutAnalyzer(script),
        router=_TesseractRouter(TesseractEngine(language)),
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


def create_ensemble_pipeline(
    tesseract_language: str,
    kraken_model_path: str,
    script: Script = Script.POLYTONIC,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
    vlm_api_key: str | None = None,
    vlm_api_url: str | None = None,
    vlm_model: str = "gpt-4o-mini",
    calamari_model_glob: str | None = None,
) -> PipelineOrchestrator:
    """Build a CPU ensemble with script rules injected at the composition root.

    When ``vlm_api_key`` is provided, a VLM engine is added as an opt-in
    second opinion for polytonic/ancient scripts. When ``calamari_model_glob``
    is provided, a Calamari subprocess engine is added as a voting booster.
    Both are wrapped in ``RetryingEngine`` for transient-failure resilience.
    """
    tesseract = RetryingEngine(TesseractEngine(tesseract_language))
    kraken = RetryingEngine(KrakenEngine(kraken_model_path))
    by_script: dict[Script, tuple] = {
        Script.ANCIENT: (kraken, tesseract),
        Script.BYZANTINE: (kraken, tesseract),
        Script.POLYTONIC: (kraken, tesseract),
    }
    default: tuple = (tesseract,)

    if vlm_api_key is not None:
        vlm = VLMEngine(api_key=vlm_api_key, api_url=vlm_api_url or "https://api.openai.com/v1", model=vlm_model)
        retrying_vlm = RetryingEngine(vlm)
        for script_key in (Script.ANCIENT, Script.BYZANTINE, Script.POLYTONIC):
            by_script[script_key] = (*by_script[script_key], retrying_vlm)

    if calamari_model_glob is not None:
        calamari = CalamariEngine(model_glob=calamari_model_glob)
        retrying_calamari = RetryingEngine(calamari)
        for script_key in (Script.ANCIENT, Script.BYZANTINE, Script.POLYTONIC):
            by_script[script_key] = (*by_script[script_key], retrying_calamari)

    router = ScriptRouter(by_script=by_script, default=default)
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=KrakenLayoutAnalyzer(script),
        router=router,
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


class _TesseractRouter:
    def __init__(self, engine: TesseractEngine) -> None:
        self._engine = engine

    def route(self, line: OCRLine, context: TenantContext) -> tuple[TesseractEngine, ...]:
        return (self._engine,)
