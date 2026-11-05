"""Training data exporter — emits line crops + transcriptions consumable by ``ketos train``.

``ketos train --format-type path`` reads each line image together with a
sidecar sharing its name up to the last extension and ending ``.gt.txt``::

    images/line-1.png
    images/line-1.gt.txt      # the transcription of line-1.png

This exporter copies line-crop images to an ``images/`` subdirectory, writes
each sidecar, and also writes a manifest, ``train.json``, listing every pair::

    [
        {"image": "images/line-1.png", "text": "κεφάλαιον"},
        {"image": "images/line-2.png", "text": "next line text"}
    ]

For training pipeline use; ALTO XML is produced by
``omniocr.infrastructure.exporters.AltoXmlExporter`` for the export stage,
not for training data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from omniocr.domain.errors import TrainingError
from omniocr.domain.result import Ok, Result
from omniocr.domain.training import TrainingSample
from omniocr.ports.interfaces import ITrainingDataExporter


class TrainingDataExporter(ITrainingDataExporter):
    """Export training samples as line-crop images + JSON manifest for ketos.

    Produces a directory structure::

        output/
        ├── images/        # Line crop images
        └── train.json     # ketos-compatible manifest
    """

    def export(self, samples: Sequence[TrainingSample], out: Path) -> Result[Path, TrainingError]:
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
            # ``with_suffix`` swaps ``.png`` for ``.gt.txt``: the sidecar ketos pairs by name.
            dest.with_suffix(".gt.txt").write_text(sample.text + "\n", encoding="utf-8")
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
