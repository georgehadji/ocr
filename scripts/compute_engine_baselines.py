"""Compute real-engine CER/WER baselines against the fixture corpus.

Unlike ``scripts/compute_baselines.py`` — which records identity baselines
(hypothesis == reference) and therefore only exercises the regression
*harness* — this script runs an actual OCR engine over each fixture image
and records what the engine genuinely scored.

Run from the repository root, with Tesseract installed and the ``ell`` and
``grc`` language packs present:

    python scripts/compute_engine_baselines.py

Output lands in ``tests/corpus/engine_baselines.json`` and is consumed by
``tests/test_engine_accuracy.py`` as a regression ceiling.

Re-run and commit the result whenever the engine, its models, or the
preprocessing chain change, and review the diff — a rise in CER is an
accuracy regression, not a baseline to rubber-stamp.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from omniocr.application.metrics import character_error_rate, word_error_rate
from omniocr.domain.models import TenantContext
from omniocr.infrastructure.tesseract import TesseractEngine
from omniocr.testing.fixtures import (
    list_fixture_ids,
    load_fixture_ground_truth,
    load_fixture_image_bytes,
)

OUTPUT = Path("tests/corpus/engine_baselines.json")

# Fixture id prefix -> Tesseract language pack.
SCRIPT_LANGUAGES: dict[str, str] = {
    "modern": "ell",
    "polytonic": "grc",
    "ancient": "grc",
    "byzantine": "grc",
}


class _FixturePage:
    """Minimal ``RawPage`` for driving an engine over fixture bytes."""

    def __init__(self, content: bytes) -> None:
        self.number = 1
        self.content = content
        self.width = 800
        self.height = 180


def language_for(fixture_id: str) -> str:
    return SCRIPT_LANGUAGES.get(fixture_id.split("-")[0], "grc")


def normalize(text: str) -> str:
    """NFC-normalize and collapse whitespace for comparison.

    Whitespace is collapsed because the engine emits word boxes that we
    join with single spaces; line breaks in the ground truth are a layout
    concern, measured separately, not a recognition error.
    """
    return unicodedata.normalize("NFC", " ".join(text.split()))


def measure(fixture_id: str) -> dict[str, float]:
    reference = normalize(load_fixture_ground_truth(fixture_id))
    engine = TesseractEngine(language=language_for(fixture_id))
    result = engine.extract(_FixturePage(load_fixture_image_bytes(fixture_id)), _CONTEXT)
    if not result.is_ok():
        raise RuntimeError(f"{fixture_id}: engine failed: {result.error}")
    hypothesis = normalize(" ".join(block.text for block in result.value))
    return {
        "cer": round(character_error_rate(reference, hypothesis), 4),
        "wer": round(word_error_rate(reference, hypothesis), 4),
    }


_CONTEXT = TenantContext(organization_id="baseline", user_id="script", subscription_tier="desktop")


def main() -> None:
    baselines = {
        fixture_id: {"tesseract": measure(fixture_id)} for fixture_id in sorted(list_fixture_ids())
    }
    OUTPUT.write_text(json.dumps(baselines, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for fixture_id, entry in baselines.items():
        scores = entry["tesseract"]
        print(f"{fixture_id:14s} CER={scores['cer']:.4f} WER={scores['wer']:.4f}")
    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":
    main()
