"""Real-engine accuracy regression tests (audit defect D7).

``test_regression.py`` verifies the regression *harness* using identity
fixtures (hypothesis == reference, so CER=0 by construction). It cannot
detect an accuracy regression in an actual OCR engine.

These tests close that gap: they run a real engine over the rendered
Greek fixture pages, compute genuine CER/WER, and gate on the committed
per-engine baselines in ``tests/corpus/engine_baselines.json``.

Tesseract and its ``ell``/``grc`` language packs are optional, so every
test skips (rather than fails) when the engine is unavailable — a base
install without the ``tesseract`` extra must still get a green suite.
Regenerate baselines with ``scripts/compute_engine_baselines.py``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import unicodedata
from functools import lru_cache
from pathlib import Path

import pytest

from omniocr.application.metrics import (
    RegressionBaseline,
    character_error_rate,
    regression_exceeded,
    word_error_rate,
)
from omniocr.domain.models import TenantContext
from omniocr.testing.fixtures import (
    fixture_tier,
    list_fixture_ids,
    list_scan_ids,
    load_fixture_ground_truth,
    load_fixture_image_bytes,
)

# Absolute ceiling proving the engine genuinely recognized Greek rather
# than emitting noise. Deliberately looser than the committed baselines:
# baselines catch drift, this catches catastrophic failure (wrong language
# pack, unreadable render, silently-empty output).
#
# Tiered, because the two tiers are different problems. A synthetic Arial
# render is trivial — Tesseract scores 0.0000-0.0135 on the four of them, so
# anything past 0.15 there means something broke. Real 20c polytonic serif
# scans measure 0.1065-0.1854 (2026-09-03, tests/corpus/engine_baselines.json),
# so the synthetic ceiling would fail a page the engine is reading correctly-
# for-its-difficulty. 0.30 still catches noise, which lands past 0.70.
MAX_PLAUSIBLE_CER: dict[str, float] = {"synthetic": 0.15, "scan": 0.30}
DEFAULT_MAX_PLAUSIBLE_CER = 0.15

# Allowance for minor Tesseract/model version differences across machines
# and CI runners. Tight enough that a real regression still trips it.
TOLERANCE = 0.05

SCRIPT_LANGUAGES: dict[str, str] = {
    "modern": "ell",
    "polytonic": "grc",
    "ancient": "grc",
    "byzantine": "grc",
}

BASELINE_FILE = Path(__file__).parent / "corpus" / "engine_baselines.json"


class _FixturePage:
    def __init__(self, content: bytes) -> None:
        self.number = 1
        self.content = content
        self.width = 800
        self.height = 180


def _tesseract_languages() -> set[str]:
    """Return installed Tesseract language packs, empty if unavailable."""
    import subprocess

    binary = shutil.which("tesseract")
    if binary is None:
        return set()
    try:
        completed = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def _has_pytesseract() -> bool:
    """Report whether the Python binding TesseractEngine imports is installed."""
    return importlib.util.find_spec("pytesseract") is not None


_AVAILABLE_LANGUAGES = _tesseract_languages()

# Both halves matter. The binary alone is not enough: TesseractEngine calls it
# through pytesseract, which ships in the `tesseract` extra, not `dev`. Gating
# on the binary alone turned an absent binding into nine failures on any box
# that has tesseract installed but the extra uninstalled.
requires_tesseract = pytest.mark.skipif(
    not ({"ell", "grc"} <= _AVAILABLE_LANGUAGES and _has_pytesseract()),
    reason="tesseract with 'ell'/'grc' language packs and the pytesseract binding not installed",
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def _language_for(fixture_id: str) -> str:
    return SCRIPT_LANGUAGES.get(fixture_id.split("-")[0], "grc")


def _load_engine_baselines() -> dict[str, dict[str, dict[str, float]]]:
    with open(BASELINE_FILE, encoding="utf-8") as handle:
        data: dict[str, dict[str, dict[str, float]]] = json.load(handle)
    return data


@lru_cache(maxsize=None)
def _recognize(fixture_id: str) -> str:
    """Run the real Tesseract engine over a fixture and return its text.

    Cached: recognition is a pure function of the fixture, and three tests
    ask for the same page. On the synthetic renders that was cheap; on a
    300 DPI scan it is tens of seconds each, so the suite was spending
    minutes re-deriving identical strings.
    """
    from omniocr.composition.desktop import _default_image_processor
    from omniocr.infrastructure.tesseract import TesseractEngine

    context = TenantContext(organization_id="test", user_id="test", subscription_tier="desktop")
    # Preprocess exactly as the pipeline does, from the same source, so a
    # preprocessing regression trips this gate. Measuring the engine on raw
    # bytes measured something the product does not do: despeckle is a 14.8%
    # relative CER improvement these baselines were entirely blind to, and
    # they would not have caught its removal either.
    raw = _FixturePage(load_fixture_image_bytes(fixture_id))
    processed = _default_image_processor().process(raw, context)
    assert processed.is_ok(), f"{fixture_id}: preprocessing failed: {processed.error}"

    engine = TesseractEngine(language=_language_for(fixture_id))
    result = engine.extract(_FixturePage(processed.value.content), context)
    assert result.is_ok(), f"{fixture_id}: engine failed: {result.error}"
    return _normalize(" ".join(block.text for block in result.value))


def test_engine_baselines_cover_every_fixture() -> None:
    """Every corpus fixture must have a committed real-engine baseline."""
    baselines = _load_engine_baselines()
    missing = [fid for fid in list_fixture_ids() if fid not in baselines]
    assert not missing, (
        f"fixtures missing engine baselines: {missing} — run scripts/compute_engine_baselines.py"
    )


@requires_tesseract
@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_tesseract_actually_recognizes_greek(fixture_id: str) -> None:
    """The engine must produce text close to ground truth, not noise.

    This is the assertion that was missing entirely: it fails if the
    engine returns nothing, returns Latin transliteration, or is pointed
    at the wrong language pack.
    """
    reference = _normalize(load_fixture_ground_truth(fixture_id))
    hypothesis = _recognize(fixture_id)

    assert hypothesis, f"{fixture_id}: engine produced no text"
    tier = fixture_tier(fixture_id)
    ceiling = MAX_PLAUSIBLE_CER.get(tier, DEFAULT_MAX_PLAUSIBLE_CER)
    cer = character_error_rate(reference, hypothesis)
    assert cer < ceiling, (
        f"{fixture_id}: CER {cer:.4f} exceeds plausible ceiling "
        f"{ceiling} for tier '{tier}' — engine is not reading this script"
    )


@requires_tesseract
@pytest.mark.parametrize("fixture_id", list_fixture_ids())
def test_tesseract_accuracy_has_not_regressed(fixture_id: str) -> None:
    """Gate real measured CER/WER against the committed engine baseline."""
    reference = _normalize(load_fixture_ground_truth(fixture_id))
    hypothesis = _recognize(fixture_id)
    entry = _load_engine_baselines()[fixture_id]["tesseract"]
    baseline = RegressionBaseline(cer=entry["cer"], wer=entry["wer"])

    assert not regression_exceeded(reference, hypothesis, baseline, tolerance=TOLERANCE), (
        f"{fixture_id}: accuracy regressed — "
        f"CER {character_error_rate(reference, hypothesis):.4f} "
        f"(baseline {baseline.cer:.4f}), "
        f"WER {word_error_rate(reference, hypothesis):.4f} "
        f"(baseline {baseline.wer:.4f}), tolerance {TOLERANCE}"
    )


@requires_tesseract
def test_polytonic_diacritics_survive_recognition() -> None:
    """Polytonic breathing/accent marks must appear in engine output.

    Guards the core product claim: a run that silently stripped diacritics
    would still score a deceptively low CER on character count alone.
    """
    hypothesis = _recognize("polytonic-1")
    decomposed = unicodedata.normalize("NFD", hypothesis)
    combining = {ch for ch in decomposed if unicodedata.combining(ch)}

    assert combining, "polytonic recognition produced no combining diacritics at all"


@pytest.mark.slow
@requires_tesseract
@pytest.mark.skipif(not list_scan_ids(), reason="no scan-tier fixture in the corpus")
def test_kraken_beats_tesseract_on_the_scan_corpus() -> None:
    """BUILD_PLAN §10 Phase 1: Kraken must beat Tesseract on real polytonic print.

    **SLOW** — Kraken model inference on CPU can take several minutes. Run
    with ``--runslow`` or use ``pytest -m slow``.

    Was ``xfail`` until 2026-09-03: the assertion was always correct, the
    corpus was not. The synthetic fixtures are Arial renders, out of domain
    for models trained on 19c serif print, so Kraken scored CER 0.70 there
    against Tesseract's 0.00 — a fact about the fixture, not the engine.

    Aggregate rather than per-fixture, and deliberately so. Measured over the
    three scan fixtures (2026-09-03, model greek-german_serifs_bsb10234118):

        scan-1  Kraken 0.1066  Tesseract 0.1366
        scan-2  Kraken 0.1940  Tesseract 0.1854   <- Tesseract wins this page
        scan-3  Kraken 0.0780  Tesseract 0.1065

    Kraken wins the corpus and loses one page. A per-fixture gate would have
    to exclude scan-2 to stay green, and excluding the page that disagrees is
    how a suite starts asserting what we wish were true. The claim in
    CLAUDE.md rule 2 is about the corpus, so the gate is too.
    """
    from omniocr.infrastructure.kraken import KrakenEngine
    from omniocr.infrastructure.model_manifest import MANIFEST_PATH, MODELS_DIR, ModelManifest

    # Rule 2: never select a model by filename order. The default is declared.
    entry = ModelManifest(MANIFEST_PATH).default_for("kraken")
    engine = KrakenEngine(str(MODELS_DIR / entry.name))
    context = TenantContext(organization_id="test", user_id="test", subscription_tier="desktop")

    kraken_cers: list[float] = []
    tesseract_cers: list[float] = []
    for fixture_id in list_scan_ids():
        reference = _normalize(load_fixture_ground_truth(fixture_id))
        result = engine.extract(_FixturePage(load_fixture_image_bytes(fixture_id)), context)
        assert result.is_ok(), f"{fixture_id}: kraken failed: {result.error}"
        kraken_cers.append(
            character_error_rate(reference, _normalize(" ".join(b.text for b in result.value)))
        )
        tesseract_cers.append(character_error_rate(reference, _recognize(fixture_id)))

    kraken_mean = sum(kraken_cers) / len(kraken_cers)
    tesseract_mean = sum(tesseract_cers) / len(tesseract_cers)
    assert kraken_mean <= tesseract_mean, (
        f"Kraken mean CER {kraken_mean:.4f} worse than Tesseract {tesseract_mean:.4f} "
        f"over {len(kraken_cers)} scan fixture(s) — the default model no longer "
        f"earns its place in models/manifest.json"
    )
