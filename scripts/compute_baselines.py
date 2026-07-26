"""Compute committed CER/WER baselines for the fixture corpus.

Run from the repository root after fixtures are generated:

    python scripts/compute_baselines.py

This uses *perfect recognition* (hypothesis == ground truth) to produce
baseline values of 0.0 for synthetic fixtures. When real OCR engines run
against the fixtures, update this script or add an engine-specific mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omniocr.application.metrics import character_error_rate, word_error_rate

CORPUS = Path("tests/corpus")


def _main() -> None:
    fixture_ids: list[str] = sorted(
        path.stem
        for path in CORPUS.glob("*.txt")
        if path.stem != "baselines"
    )

    baselines: dict[str, dict[str, float]] = {}
    for fixture_id in fixture_ids:
        reference = Path(CORPUS / f"{fixture_id}.txt").read_text(encoding="utf-8")
        hypothesis = reference  # perfect recognition for synthetic fixtures
        cer = character_error_rate(reference, hypothesis)
        wer = word_error_rate(reference, hypothesis)
        baselines[fixture_id] = {"cer": cer, "wer": wer}
        print(f"  {fixture_id}: CER={cer:.4f}  WER={wer:.4f}")

    path = CORPUS / "baselines.json"
    path.write_text(json.dumps(baselines, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {len(baselines)} baseline(s) to {path}")


if __name__ == "__main__":
    _main()
