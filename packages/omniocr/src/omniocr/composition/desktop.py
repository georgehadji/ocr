from __future__ import annotations

from pathlib import Path

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.application.structure import DocumentAssembler, IdentityAssembler
from omniocr.application.layout import FallbackLayoutAnalyzer
from omniocr.infrastructure.config import DEFAULT_TESSERACT_LANGUAGE
from omniocr.infrastructure.tesseract import TesseractEngine, TesseractLayoutAnalyzer
from omniocr.infrastructure.ingest import DocumentPageSource
from omniocr.infrastructure.preprocess import (
    ChainProcessor,
    DespeckleProcessor,
    GrayscaleProcessor,
    OtsuProcessor,
    SauvolaProcessor,
)
from omniocr.infrastructure.resilience import RetryingEngine
from omniocr.application.reconcile import (
    AlignedReconciler,
    ConfidenceWeightedReconciler,
    ScriptAwareReconciler,
)
from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.jobs import InMemoryJobStore
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.ports.interfaces import IExporter, IImageProcessor, IJobStore, IOCREngine

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


def _variant_processors(count: int) -> tuple[tuple[str, IImageProcessor], ...]:
    """Extra preprocessing variants beyond the primary (ENHANCEMENT_PLAN A3).

    ``count`` is the *total* number of images each page is recognized from, so
    1 means the primary alone and no extra cost. Each variant multiplies
    recognition time, which dominates a run, so this stays opt-in.

    The variants are binarizations rather than geometric transforms, and that
    is a hard constraint rather than a preference: layout is segmented once on
    the primary page and every variant's word boxes are matched against those
    segments, so anything that rotated or rescaled would misassign every block.
    The pipeline rejects such a variant defensively, but there is no reason to
    offer one here.

    Ordered by expected usefulness, so ``--variants 2`` gets the most valuable
    addition. Otsu comes first because it is a global threshold where Sauvola
    is local: the two fail on opposite kinds of page — Otsu on uneven
    lighting, Sauvola by inventing texture in blank margins — and their
    disagreement is exactly what A4's merge turns into signal.
    """
    if count < 1:
        raise ValueError("variants must be >= 1 (1 means the primary page alone)")
    available: tuple[tuple[str, IImageProcessor], ...] = (
        ("otsu", ChainProcessor(GrayscaleProcessor(), OtsuProcessor())),
        ("sauvola", ChainProcessor(GrayscaleProcessor(), SauvolaProcessor())),
        (
            "despeckled-otsu",
            ChainProcessor(GrayscaleProcessor(), DespeckleProcessor(), OtsuProcessor()),
        ),
    )
    return available[: count - 1]


def _default_image_processor() -> ChainProcessor:
    """Grayscale then despeckle — the chain the accuracy measurement earned.

    Measured 2026-09-03 with Tesseract ``grc`` over the three scan-tier
    fixtures (mean of ``polytonic-scan-1/2/3``):

        grayscale only          CER 0.1436  WER 0.4479
        grayscale + despeckle   CER 0.1224  WER 0.3685
        grayscale + deskew      CER 0.1491  WER 0.4480

    Despeckle is a 14.8% relative CER improvement and wins every page.

    Deskew is deliberately *not* here despite being the more obvious stage.
    It made things worse: these three pages are already square, so the
    estimator buys an interpolation pass and a slightly wrong angle for
    nothing. That is a fact about this corpus, not about deskewing — the
    stage recovers a known skew to within 0.2 degrees in
    ``tests/test_preprocess_geometry.py``. It stays available for callers
    with skewed input; it does not become a default on evidence that says
    it costs accuracy. Revisit when a scan-tier fixture is actually
    crooked.
    """
    return ChainProcessor(GrayscaleProcessor(), DespeckleProcessor())


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
        image_processor=_default_image_processor(),
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
    variants: int = 1,
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
        image_processor=_default_image_processor(),
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
        # AlignedReconciler delegates the choice to ScriptAwareReconciler and
        # adds A4's word-level merge as a suggestion. It chooses nothing
        # differently; it only offers the merge for review.
        reconciler=AlignedReconciler(ScriptAwareReconciler()),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        assembler=assembler,
        job_store=job_store or InMemoryJobStore(),
        max_workers=workers if workers > 1 else None,
        variants=_variant_processors(variants),
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
