"""Assembling training data from human-verified corrections.

Fixes D9: the assembler maps each ``GroundTruthLine`` through ``ILineCropper``
to a **line-level image**, never a page. ``SampleSpecification`` composes
filters (minimum height, non-empty text, confidence floor, script match) as
a Specification so rules combine without conditional sprawl.

The function is pure apart from the injected cropper — given the same
corrections it yields the same manifest, making training runs reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from omniocr.domain.corrections import Correction, GroundTruthLine
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import Script
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import TrainingSample
from omniocr.ports.interfaces import ILineCropper


@dataclass(frozen=True, slots=True)
class SampleSpecification:
    """Composable filter specification for training sample eligibility.

    All conditions must be met for a sample to pass (AND semantics).
    """

    min_line_height: int = 8
    require_non_empty: bool = True
    allowed_scripts: tuple[Script, ...] = ()
    min_confidence: float = 0.0

    def is_satisfied_by(self, correction: Correction) -> bool:
        """Check whether a correction meets all specification criteria."""
        if self.require_non_empty and not correction.corrected_text.strip():
            return False
        if self.allowed_scripts and correction.script not in self.allowed_scripts:
            return False
        return True


DEFAULT_SPEC = SampleSpecification()


def assemble_training_samples(
    corrections: Sequence[Correction],
    cropper: ILineCropper,
    page_images: dict[int, bytes],
    output_dir: Path,
    spec: SampleSpecification = DEFAULT_SPEC,
) -> Result[Sequence[TrainingSample], TrainingError]:
    """Assemble line-crop training samples from human-verified corrections.

    Each accepted correction is converted to a ``GroundTruthLine``, cropped
    from its page image, and written to ``output_dir`` as a line-level image.

    Args:
        corrections: Human-verified transcriptions.
        cropper: Adapter that crops a page image to a line ``BBox``.
        page_images: Map of page number → raw image bytes.
        output_dir: Where cropped line images will be written.
        spec: Filter specification for sample eligibility.

    Returns:
        A list of ``TrainingSample`` references (image_path, text, script),
        or an error if any stage fails.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    samples: list[TrainingSample] = []
    for correction in corrections:
        ground_truth = GroundTruthLine.from_correction(correction)
        if ground_truth is None:
            continue
        if not spec.is_satisfied_by(correction):
            continue

        page_bytes = page_images.get(correction.page_number)
        if page_bytes is None:
            return Err(
                TrainingError(
                    f"no page image available for page {correction.page_number} "
                    f"(line {correction.line_id})"
                )
            )

        crop_result = cropper.crop(page_bytes, correction.bbox)
        if isinstance(crop_result, Err):
            return Err(crop_result.error)

        crop_bytes = crop_result.value
        image_name = f"line-{correction.line_id}.png"
        image_path = output / image_name
        image_path.write_bytes(crop_bytes)

        samples.append(
            TrainingSample(
                image_path=image_path,
                text=ground_truth.text,
                script=ground_truth.script,
            )
        )

    return Ok(samples)


__all__ = [
    "DEFAULT_SPEC",
    "SampleSpecification",
    "assemble_training_samples",
]
