from __future__ import annotations

from pathlib import Path

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.application.structure import DocumentAssembler, IdentityAssembler
from omniocr.application.layout import FallbackLayoutAnalyzer
from omniocr.infrastructure.config import DEFAULT_TESSERACT_LANGUAGE
from omniocr.infrastructure.tesseract import TesseractEngine, TesseractLayoutAnalyzer
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

# Defaults only — no optional third-party imports in vlm's module scope, so
# these resolve even when the VLM extra is absent. They are function-signature
# defaults, which are evaluated at import time and so cannot live in the
# guarded block below.
from omniocr.infrastructure.vlm import DEFAULT_API_URL as DEFAULT_VLM_API_URL
from omniocr.infrastructure.vlm import DEFAULT_MAX_EDGE_PX as DEFAULT_VLM_MAX_EDGE_PX
from omniocr.infrastructure.vlm import DEFAULT_MODEL as DEFAULT_VLM_MODEL

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
    language: str = DEFAULT_TESSERACT_LANGUAGE,
    script: Script = Script.MODERN,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
    assemble_structure: bool = False,
    workers: int = 1,
) -> PipelineOrchestrator:
    """Build the desktop pipeline with the optional Tesseract engine enabled."""
    assembler = DocumentAssembler() if assemble_structure else IdentityAssembler()
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        layout_analyzer=FallbackLayoutAnalyzer(
            KrakenLayoutAnalyzer(script), TesseractLayoutAnalyzer(language, script)
        ),
        router=_TesseractRouter(TesseractEngine(language)),
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        assembler=assembler,
        job_store=job_store or InMemoryJobStore(),
        max_workers=workers if workers > 1 else None,
    )


def create_ensemble_pipeline(
    tesseract_language: str,
    kraken_model_path: str,
    script: Script = Script.POLYTONIC,
    exporter: IExporter | None = None,
    job_store: IJobStore | None = None,
    vlm_api_key: str | None = None,
    vlm_api_url: str | None = None,
    vlm_model: str = DEFAULT_VLM_MODEL,
    vlm_max_edge_px: int = DEFAULT_VLM_MAX_EDGE_PX,
    calamari_model_glob: str | None = None,
    assemble_structure: bool = False,
    workers: int = 1,
) -> PipelineOrchestrator:
    """Build a CPU ensemble with script rules injected at the composition root.

    When ``vlm_api_key`` is provided, a VLM engine is added as an opt-in
    second opinion for polytonic/ancient scripts. The VLM endpoint and model
    default to ``vlm.DEFAULT_API_URL`` / ``vlm.DEFAULT_VLM_MODEL`` (OpenRouter)
    — set ``vlm_api_url`` to use a different OpenAI-compatible provider. The
    defaults are not restated here: this root previously hardcoded an OpenAI
    fallback URL while passing an OpenRouter model id, which sent
    ``google/...`` to a provider that does not serve it. When
    ``calamari_model_glob``
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
            api_url=vlm_api_url or DEFAULT_VLM_API_URL,
            model=vlm_model,
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
    # Keys come from each engine's own ``name`` so the map cannot drift from
    # the identity the router and manifest match on.
    engine_map: dict[str, IOCREngine] = {
        KrakenEngine.name: kraken,
        TesseractEngine.name: tesseract,
    }
    if vlm_api_key is not None:
        engine_map[VLMEngine.name] = retrying_vlm
    if calamari_model_glob is not None:
        engine_map[CalamariEngine.name] = retrying_calamari

    router = ScriptRouter(
        by_script=by_script,
        default=default,
        engine_map=engine_map,
        engine_factory=_build_engine_on_checkpoint,
    )
    assembler = DocumentAssembler() if assemble_structure else IdentityAssembler()
    return PipelineOrchestrator(
        page_source=DocumentPageSource(),
        image_processor=GrayscaleProcessor(),
        # Kraken segmentation returns zero lines with no error on grainy
        # scans; without a fallback those pages vanish from the output.
        layout_analyzer=FallbackLayoutAnalyzer(
            KrakenLayoutAnalyzer(script), TesseractLayoutAnalyzer(tesseract_language, script)
        ),
        router=router,
        # Kraken leads on Greek but its charset holds no Latin, so a plain
        # confidence vote hands Latin lines (footnote URLs, western-language
        # citations) to a model that cannot spell them. Pass Latin packs in
        # ``tesseract_language`` for this to have anything to choose.
        reconciler=ScriptAwareReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        assembler=assembler,
        job_store=job_store or InMemoryJobStore(),
        max_workers=workers if workers > 1 else None,
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
