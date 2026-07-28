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

import json
import shutil
import unicodedata
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
    list_fixture_ids,
    load_fixture_ground_truth,
    load_fixture_image_bytes,
)

# Absolute ceiling proving the engine genuinely recognized Greek rather
# than emitting noise. Deliberately looser than the committed baselines:
# baselines catch drift, this catches catastrophic failure (wrong language
# pack, unreadable render, silently-empty output).
MAX_PLAUSIBLE_CER = 0.15

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


_AVAILABLE_LANGUAGES = _tesseract_languages()

requires_tesseract = pytest.mark.skipif(
    not {"ell", "grc"} <= _AVAILABLE_LANGUAGES,
    reason="tesseract with 'ell' and 'grc' language packs not installed",
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def _language_for(fixture_id: str) -> str:
    return SCRIPT_LANGUAGES.get(fixture_id.split("-")[0], "grc")


def _load_engine_baselines() -> dict[str, dict[str, dict[str, float]]]:
    with open(BASELINE_FILE, encoding="utf-8") as handle:
        data: dict[str, dict[str, dict[str, float]]] = json.load(handle)
    return data


def _recognize(fixture_id: str) -> str:
    """Run the real Tesseract engine over a fixture and return its text."""
    from omniocr.infrastructure.tesseract import TesseractEngine

    engine = TesseractEngine(language=_language_for(fixture_id))
    context = TenantContext(organization_id="test", user_id="test", subscription_tier="desktop")
    result = engine.extract(_FixturePage(load_fixture_image_bytes(fixture_id)), context)
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
    cer = character_error_rate(reference, hypothesis)
    assert cer < MAX_PLAUSIBLE_CER, (
        f"{fixture_id}: CER {cer:.4f} exceeds plausible ceiling "
        f"{MAX_PLAUSIBLE_CER} — engine is not reading this script"
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


@pytest.mark.skipif(
    not list(Path("models").glob("*.mlmodel")),
    reason="no Kraken .mlmodel available; see models/README.md",
)
@pytest.mark.parametrize("fixture_id", ["polytonic-1", "ancient-1"])
def test_kraken_beats_tesseract_on_hard_scripts(fixture_id: str) -> None:
    """BUILD_PLAN §10 Phase 1: Kraken must beat Tesseract on polytonic/ancient.

    Skipped until a Greek Kraken model is committed to ``models/``. This
    acceptance criterion is therefore NOT yet verified — see audit D7.
    """
    from omniocr.infrastructure.kraken import KrakenEngine

    model = next(iter(Path("models").glob("*.mlmodel")))
    reference = _normalize(load_fixture_ground_truth(fixture_id))
    context = TenantContext(organization_id="test", user_id="test", subscription_tier="desktop")

    kraken_result = KrakenEngine(str(model)).extract(
        _FixturePage(load_fixture_image_bytes(fixture_id)), context
    )
    assert kraken_result.is_ok(), f"kraken failed: {kraken_result.error}"
    kraken_text = _normalize(" ".join(b.text for b in kraken_result.value))

    kraken_cer = character_error_rate(reference, kraken_text)
    tesseract_cer = character_error_rate(reference, _recognize(fixture_id))

    assert kraken_cer <= tesseract_cer, (
        f"{fixture_id}: Kraken CER {kraken_cer:.4f} worse than Tesseract {tesseract_cer:.4f}"
    )
