"""Evaluation — measuring a model's accuracy against a held-out corpus split.

Reuses v1's ``application/metrics.py`` (CER, WER). Calls ``engine.extract()``
on each corpus page image to produce a hypothesis, then compares against the
ground truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence as SeqType

from omniocr.application.metrics import character_error_rate, word_error_rate
from omniocr.domain.corpus import CorpusPage, SplitName
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import Script, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import EvaluationReport
from omniocr.ports.interfaces import IOCREngine


class _CorpusRawPage:
    """Minimal RawPage adapter wrapping a corpus image for engine.extract()."""

    __slots__ = ("number", "content", "width", "height")

    def __init__(self, image_bytes: bytes) -> None:
        self.number = 1
        self.content = image_bytes
        self.width = 1
        self.height = 1


def evaluate(
    engine: IOCREngine,
    pages: SeqType[CorpusPage],
    split: SplitName,
) -> Result[EvaluationReport, TrainingError]:
    """Evaluate an OCR engine against a corpus split.

    For each page in the split, reads the image, runs the engine, and
    compares the output against the ground truth transcription.

    Args:
        engine: The OCR engine to evaluate.
        pages: Corpus pages (filtered to the requested split beforehand).
        split: The split name — used only for record-keeping, not filtering.

    Returns:
        An ``EvaluationReport`` with per-script CER/WER, or an error.
    """
    if not pages:
        return Err(TrainingError("no pages provided for evaluation"))

    context = TenantContext(
        organization_id="evaluation",
        user_id="system",
        subscription_tier="desktop",
    )

    # Group (reference, hypothesis) pairs by script
    script_texts: dict[Script, list[tuple[str, str]]] = {}
    for page in pages:
        if page.split != split:
            continue

        # Read ground truth
        gt_text = _read_ground_truth(page.ground_truth_path)
        if gt_text is None:
            return Err(TrainingError(f"cannot read ground truth: {page.ground_truth_path}"))

        # Read the page image and run the engine
        try:
            image_bytes = page.image_path.read_bytes()
        except OSError as exc:
            return Err(TrainingError(f"cannot read image {page.image_path}: {exc}"))

        raw_page = _CorpusRawPage(image_bytes)
        result = engine.extract(raw_page, context)
        if not result.is_ok():
            return Err(
                TrainingError(
                    f"engine failed on {page.page_id}: {result.error}"  # type: ignore[attr-defined]
                )
            )

        hypothesis = " ".join(block.text for block in result.value)  # type: ignore[attr-defined]

        script_texts.setdefault(page.script, []).append((gt_text, hypothesis))

    per_script_cer: dict[Script, float] = {}
    per_script_wer: dict[Script, float] = {}
    total_samples = sum(len(texts) for texts in script_texts.values())

    for script, texts in script_texts.items():
        if not texts:
            continue
        cers = [character_error_rate(ref, hyp) for ref, hyp in texts if hyp]
        wers = [word_error_rate(ref, hyp) for ref, hyp in texts if hyp]
        per_script_cer[script] = sum(cers) / len(cers) if cers else 1.0
        per_script_wer[script] = sum(wers) / len(wers) if wers else 1.0

    return Ok(
        EvaluationReport(
            model_hash=engine.name,
            per_script_cer=per_script_cer,
            per_script_wer=per_script_wer,
            sample_count=total_samples,
            evaluated_on=split.value,
        )
    )


def _read_ground_truth(path: Path) -> str | None:
    """Read the ground truth text from a corpus file."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None


__all__ = ["evaluate"]
