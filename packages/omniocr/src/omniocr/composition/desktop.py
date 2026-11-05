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
from omniocr.infrastructure.grounding import GroundedEngine
from omniocr.infrastructure.model_manifest import MANIFEST_PATH, ModelManifest
from omniocr.infrastructure.resilience import CircuitBreakerEngine, RetryingEngine
from omniocr.application.reconcile import (
    AlignedReconciler,
    ConfidenceWeightedReconciler,
    ScriptAwareReconciler,
)
from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.application.router import ScriptRouter
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer
from omniocr.infrastructure.exporters import MarkdownExporter
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.ports.interfaces import IExporter, IImageProcessor, IOCREngine

# Defaults only — no optional third-party imports in vlm's module scope, so
# these resolve even when the VLM extra is absent. They are function-signature
# defaults, which are evaluated at import time and so cannot live in the
# guarded block below.
from omniocr.infrastructure.vlm import DEFAULT_API_URL as DEFAULT_VLM_API_URL
from omniocr.infrastructure.vlm import DEFAULT_MAX_EDGE_PX as DEFAULT_VLM_MAX_EDGE_PX
from omniocr.infrastructure.vlm import DEFAULT_MODEL as DEFAULT_VLM_MODEL
from omniocr.infrastructure.unlimited_ocr import DEFAULT_MODEL as DEFAULT_UNLIMITED_OCR_MODEL

# The scripts the ensemble routes to Kraken and the opt-in voters; every other
# script falls through to Tesseract alone.
ENSEMBLE_SCRIPTS = (Script.ANCIENT, Script.BYZANTINE, Script.POLYTONIC)

# VLM and Calamari are optional extras; import failures gracefully disable them.
try:
    from omniocr.infrastructure.calamari import CalamariEngine  # noqa: F811

    _CALAMARI_AVAILABLE = True
except ImportError:
    _CALAMARI_AVAILABLE = False

try:
    from omniocr.infrastructure.unlimited_ocr import UnlimitedOCREngine
    from omniocr.infrastructure.vlm import VLMEngine  # noqa: F811

    _VLM_AVAILABLE = True
except ImportError:
    _VLM_AVAILABLE = False


def _verify_against_manifest(model_path: Path) -> None:
    """Refuse a Kraken model whose bytes differ from its pinned manifest hash.

    A model the manifest does not list (a fine-tuned checkpoint, a user's own
    ``--model``) is unverifiable, not wrong, so it passes. Only a listed model
    that no longer matches is refused: that is a corrupt or swapped artifact
    that would silently change accuracy by the 7x the manifest exists to pin.
    """
    manifest = ModelManifest(MANIFEST_PATH)
    entry = manifest.get(model_path.name)
    if entry is not None and model_path.is_file() and not manifest.verify(entry.name, model_path):
        raise ValueError(
            f"Kraken model {model_path} does not match the SHA-256 pinned in {MANIFEST_PATH}"
        )


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
    if count == 2:
        # Measured 2026-09-05: with exactly two candidates every disagreement
        # is a 1-1 tie, ties go to the pivot, and the merge is a guaranteed
        # no-op — 0 merges fired across 136 disagreeing lines. The second
        # recognition pass is pure cost, and it also makes the *chosen* line
        # worse, because the reconciler falls back to confidence and Tesseract
        # is confidently wrong on binarized input. Refusing beats silently
        # doubling the runtime for a negative return.
        raise ValueError(
            "variants=2 cannot improve anything: two candidates always tie and the "
            "tie goes to the pivot, so the merge is a no-op while costing a second "
            "recognition pass. Use 1 (no ensembling) or >=3 (a majority can outvote "
            "the pivot). See docs/ENGINE_ACCURACY.md."
        )
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

    These absolute figures predate the 2026-11-03 fix to the
    ``polytonic-scan-1`` reference, which was missing two lines; current
    numbers are in ``docs/ENGINE_ACCURACY.md``. The comparison between
    chains was not re-run.

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
    assemble_structure: bool = False,
    workers: int = 1,
    text_layer: bool = True,
) -> PipelineOrchestrator:
    """Build the desktop pipeline with the optional Tesseract engine enabled."""
    assembler = DocumentAssembler() if assemble_structure else IdentityAssembler()
    return PipelineOrchestrator(
        page_source=DocumentPageSource(text_layer=text_layer, script=script),
        image_processor=_default_image_processor(),
        layout_analyzer=FallbackLayoutAnalyzer(
            KrakenLayoutAnalyzer(script), TesseractLayoutAnalyzer(language, script)
        ),
        router=_TesseractRouter(TesseractEngine(language)),
        reconciler=ConfidenceWeightedReconciler(),
        post_corrector=SuggestOnlyCorrector(lexicons=lexicons_by_script()),
        exporter=exporter or MarkdownExporter(),
        assembler=assembler,
        max_workers=workers if workers > 1 else None,
    )


def create_ensemble_pipeline(
    tesseract_language: str,
    kraken_model_path: str,
    script: Script = Script.POLYTONIC,
    exporter: IExporter | None = None,
    vlm_api_key: str | None = None,
    vlm_api_url: str | None = None,
    vlm_model: str = DEFAULT_VLM_MODEL,
    vlm_max_edge_px: int = DEFAULT_VLM_MAX_EDGE_PX,
    unlimited_ocr_url: str | None = None,
    unlimited_ocr_model: str = DEFAULT_UNLIMITED_OCR_MODEL,
    calamari_model_glob: str | None = None,
    assemble_structure: bool = False,
    workers: int = 1,
    variants: int = 1,
    text_layer: bool = True,
) -> PipelineOrchestrator:
    """Build a CPU ensemble with script rules injected at the composition root.

    Kraken and Tesseract (each in a ``RetryingEngine``) recognize polytonic,
    ancient and Byzantine pages; every other script goes to Tesseract alone.
    Kraken's model is checked against the pinned manifest hash first, and the
    layout analyzer falls back to Tesseract when Kraken finds no lines.

    When ``vlm_api_key`` is provided, a VLM engine is added as an opt-in
    second opinion for those scripts. The VLM endpoint and model
    default to ``vlm.DEFAULT_API_URL`` / ``vlm.DEFAULT_VLM_MODEL`` (OpenRouter)
    — set ``vlm_api_url`` to use a different OpenAI-compatible provider. The
    defaults are not restated here: this root previously hardcoded an OpenAI
    fallback URL while passing an OpenRouter model id, which sent
    ``google/...`` to a provider that does not serve it. When
    ``unlimited_ocr_url`` is provided, Unlimited-OCR-2 served at that
    OpenAI-compatible endpoint joins the same scripts as a further opt-in
    voter. Both are wrapped in ``GroundedEngine``, which drops blocks over
    regions Tesseract read as empty, so a model's unaudited output never
    reaches the reconciler. The VLM is also wrapped in ``RetryingEngine`` for
    transient-failure resilience. When ``calamari_model_glob`` is provided, a
    Calamari subprocess engine (GPLv3, never imported) joins the same scripts
    as a voter, also in a ``RetryingEngine``.
    Unlimited-OCR is not retried: its failures are mostly ten-minute timeouts
    from a busy or dead GPU server, so a retry doubles the stall and resubmits
    a job the server may still be running. A circuit breaker instead skips it
    for one timeout's length after a failure, so a dead server costs one
    timeout per window rather than two per page.
    """
    tesseract = RetryingEngine(TesseractEngine(tesseract_language))
    _verify_against_manifest(Path(kraken_model_path))
    kraken = RetryingEngine(KrakenEngine(kraken_model_path))
    by_script: dict[Script, tuple[IOCREngine, ...]] = {
        script_key: (kraken, tesseract) for script_key in ENSEMBLE_SCRIPTS
    }
    default: tuple[IOCREngine, ...] = (tesseract,)

    if vlm_api_key is not None:
        vlm = VLMEngine(
            api_key=vlm_api_key,
            api_url=vlm_api_url or DEFAULT_VLM_API_URL,
            model=vlm_model,
            max_edge_px=vlm_max_edge_px,
        )
        retrying_vlm = GroundedEngine(RetryingEngine(vlm), tesseract)
        for script_key in ENSEMBLE_SCRIPTS:
            by_script[script_key] = (*by_script[script_key], retrying_vlm)

    if unlimited_ocr_url is not None:
        guarded_unlimited = GroundedEngine(
            CircuitBreakerEngine(
                UnlimitedOCREngine(api_url=unlimited_ocr_url, model=unlimited_ocr_model),
                failure_threshold=1,
                reset_timeout=UnlimitedOCREngine.timeout_s,
            ),
            tesseract,
        )
        for script_key in ENSEMBLE_SCRIPTS:
            by_script[script_key] = (*by_script[script_key], guarded_unlimited)

    # Kraken segmentation returns zero lines with no error on grainy
    # scans; without a fallback those pages vanish from the output.
    layout_analyzer = FallbackLayoutAnalyzer(
        KrakenLayoutAnalyzer(script), TesseractLayoutAnalyzer(tesseract_language, script)
    )

    if calamari_model_glob is not None:
        # Calamari reads lines, so it gets the pipeline's own segmenter.
        retrying_calamari = RetryingEngine(
            CalamariEngine(model_glob=calamari_model_glob, layout_analyzer=layout_analyzer)
        )
        for script_key in ENSEMBLE_SCRIPTS:
            by_script[script_key] = (*by_script[script_key], retrying_calamari)

    router = ScriptRouter(by_script=by_script, default=default)
    assembler = DocumentAssembler() if assemble_structure else IdentityAssembler()
    return PipelineOrchestrator(
        page_source=DocumentPageSource(text_layer=text_layer, script=script),
        image_processor=_default_image_processor(),
        layout_analyzer=layout_analyzer,
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
        max_workers=workers if workers > 1 else None,
        variants=_variant_processors(variants),
    )


class _TesseractRouter:
    def __init__(self, engine: TesseractEngine) -> None:
        self._engine = engine

    def route(self, line: OCRLine, context: TenantContext) -> tuple[TesseractEngine, ...]:
        return (self._engine,)
