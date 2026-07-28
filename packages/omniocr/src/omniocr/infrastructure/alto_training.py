"""Training data exporter — emits JSON manifest + images consumable by ``ketos train``.

``ketos train`` accepts a flat JSON manifest with image paths and transcriptions::

    [
        {"image": "images/line-1.png", "text": "κεφάλαιον"},
        {"image": "images/line-2.png", "text": "next line text"}
    ]

This exporter copies line-crop images to an ``images/`` subdirectory and
writes the manifest as ``train.json``. For training pipeline use; ALTO XML
is produced by ``omniocr.infrastructure.exporters.AltoXmlExporter`` for the
export stage, not for training data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from omniocr.domain.errors import TrainingError
from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.training import TrainingSample
from omniocr.ports.interfaces import ITrainingDataExporter


class TrainingDataExporter(ITrainingDataExporter):
    """Export training samples as line-crop images + JSON manifest for ketos.

    Produces a directory structure::

        output/
        ├── images/        # Line crop images
        └── train.json     # ketos-compatible manifest
    """

    def export(
        self, samples: Sequence[TrainingSample], out: Path
    ) -> Result[Path, TrainingError]:
        """Export training samples to a ketos-consumable format.

        Args:
            samples: The training samples (line crops with text).
            out: Output directory for the exported training data.

        Returns:
            The path to the exported ``train.json`` manifest, or an error.
        """
        output = Path(out)
        output.mkdir(parents=True, exist_ok=True)
        images_dir = output / "images"
        images_dir.mkdir(exist_ok=True)

        records: list[dict[str, str]] = []
        for sample in samples:
            # Copy the line crop image to the images directory
            dest = images_dir / sample.image_path.name
            if sample.image_path.exists():
                dest.write_bytes(sample.image_path.read_bytes())
            records.append(
                {
                    "image": str(dest.relative_to(output)),
                    "text": sample.text,
                }
            )

        import json

        manifest_path = output / "train.json"
        manifest_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return Ok(manifest_path)


__all__ = ["TrainingDataExporter"]
