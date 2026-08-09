from __future__ import annotations

from pathlib import Path

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.infrastructure.tesseract import TesseractEngine
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import GrayscaleProcessor
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import ConfidenceWeightedReconciler, ScriptAwareReconciler
from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import InMemoryJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.ports.interfaces import IExporter, IJobStore, IOCREngine

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
    vlm_model: str = "google/gemini-3.5-flash-lite",
    vlm_max_edge_px: int = 1400,
    calamari_model_glob: str | None = None,
) -> PipelineOrchestrator:
    """Build a CPU ensemble with script rules injected at the composition root.

    When ``vlm_api_key`` is provided, a VLM engine is added as an opt-in
    second opinion for polytonic/ancient scripts. The VLM defaults to an
    OpenRouter-compatible endpoint (``https://openrouter.ai/api/v1``) with
    ``google/gemini-2.5-flash-001`` — set ``vlm_api_url`` to use a different
    OpenAI-compatible provider. When ``calamari_model_glob``
    is provided, a Calamari subprocess engine is added as a voting booster.
    Both are wrapped in ``RetryingEngine`` for transient-failure resilience.
    """
    tesseract = RetryingEngine(TesseractEngine(tesseract_language))
    kraken = RetryingEngine(KrakenEngine(kraken_model_path))
    by_script: dict[Script, tuple[IOCREngine, ...]] = {
        Script.ANCIENT: (kraken, tesseract),
        Script.BYZANTINE: (kraken, tesseract),
        Script.POLYTONIC: (kraken, tesseract),
    }
    default: tuple[IOCREngine, ...] = (tesseract,)

    if vlm_api_key is not None:
        vlm = VLMEngine(
            api_key=vlm_api_key,
            api_url=vlm_api_url or "https://api.openai.com/v1",
            model=vlm_model,
            # Ingest renders at 300 DPI for box-grounded Tesseract/Kraken; the
            # VLM bills per tile and does not need it. Tune against the
            # grounded/ungrounded ratio — see docs/VLM_COST_OPTIMIZATION.md.
            max_edge_px=vlm_max_edge_px,
        )
        retrying_vlm = RetryingEngine(vlm)
        for script_key in (Script.ANCIENT, Script.BYZANTINE, Script.POLYTONIC):
            by_script[script_key] = (*by_script[script_key], retrying_vlm)

    if calamari_model_glob is not None:
        calamari = CalamariEngine(model_glob=calamari_model_glob)
        retrying_calamari = RetryingEngine(calamari)
        for script_key in (Script.ANCIENT, Script.BYZANTINE, Script.POLYTONIC):
            by_script[script_key] = (*by_script[script_key], retrying_calamari)

    # Build engine_map so the router can resolve promoted models by engine name
    engine_map: dict[str, IOCREngine] = {
        "kraken": kraken,
        "tesseract": tesseract,
    }
    if vlm_api_key is not None:
        engine_map["vlm"] = retrying_vlm
    if calamari_model_glob is not None:
        engine_map["calamari"] = retrying_calamari

    router = ScriptRouter(
        by_script=by_script,
        default=default,
        engine_map=engine_map,
        engine_factory=_build_engine_on_checkpoint,
    )
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=KrakenLayoutAnalyzer(script),
        router=router,
        # Kraken leads on Greek but its charset holds no Latin, so a plain
        # confidence vote hands Latin lines (footnote URLs, western-language
        # citations) to a model that cannot spell them. Pass Latin packs in
        # ``tesseract_language`` for this to have anything to choose.
        reconciler=ScriptAwareReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        job_store=job_store or InMemoryJobStore(),
    )


def _build_engine_on_checkpoint(engine_family: str, checkpoint: Path) -> IOCREngine:
    """Build an engine running a promoted fine-tuned checkpoint.

    Only Kraken is fine-tunable here (``ketos``), so other families fall back
    to a Kraken engine on the checkpoint rather than silently returning the
    parent-weights engine — a promoted model must never route to the weights
    it was promoted over.
    """
    return RetryingEngine(KrakenEngine(checkpoint))


class _TesseractRouter:
    def __init__(self, engine: TesseractEngine) -> None:
        self._engine = engine

    def route(self, line: OCRLine, context: TenantContext) -> tuple[TesseractEngine, ...]:
        return (self._engine,)
