"""Optional Calamari OCR subprocess adapter with GPLv3 isolation.

Per BUILD_PLAN §4.8: Calamari is invoked as a subprocess over a temp
directory. The core package never imports ``calamari_ocr`` directly —
the license isolation CI check enforces this.

Use through the optional ``omniocr[calamari]`` extra, which installs
calamari_ocr in a separate environment or as a subprocess dependency.

Calamari reads **single text lines**, not pages. Handed a whole page it emits
one nonsense string, so this adapter first segments the page with an injected
``ILayoutAnalyzer``, crops each line, and runs ``calamari-predict`` once over
all the crops. Each line comes back as one block carrying that line's box.
"""

from __future__ import annotations

import glob
import io
import json
import os
import subprocess  # nosec B404 - argv list, no shell, isolation is the point (BUILD_PLAN §4.8)
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ILayoutAnalyzer, IOCREngine, RawPage

if TYPE_CHECKING:
    from PIL import Image

# A page of a few hundred lines pays TensorFlow's start-up once, then predicts.
_TIMEOUT_S = 600


class CalamariEngine(IOCREngine):
    """Subprocess adapter for Calamari OCR.

    Runs the ``calamari-predict`` CLI over line crops in a temp directory. The
    caller must ensure Calamari is installed and available on ``PATH`` — it is
    NOT imported into the Python process.

    ``model_glob`` names the checkpoints: a glob such as ``models/*.ckpt.h5``
    or a path without extension. Several checkpoints are voted by Calamari
    itself. Confidence is Calamari's mean character probability on the 0-100
    scale the other engines use.

    GPLv3 license note: Calamari (``calamari_ocr``) is GPLv3-licensed.
    This adapter isolates it in a subprocess so the core ``omniocr``
    package remains under its permissive license.
    """

    name = "calamari"

    def __init__(
        self,
        model_glob: str = "*.ckpt.h5",
        layout_analyzer: ILayoutAnalyzer | None = None,
        args: Sequence[str] = (),
    ) -> None:
        self._model_glob = model_glob
        self._layout_analyzer = layout_analyzer
        self._args = tuple(args)

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        if self._layout_analyzer is None:
            return Err(EngineError("Calamari reads lines and needs a layout analyzer"))
        checkpoints = self._checkpoints()
        if not checkpoints:
            return Err(EngineError(f"no Calamari checkpoint matches {self._model_glob!r}"))
        segmented = self._layout_analyzer.segment(page, context)
        if isinstance(segmented, Err):
            return Err(EngineError(f"Calamari could not segment the page: {segmented.error}"))
        if not segmented.value:
            return Ok(())

        try:
            from PIL import Image

            image = Image.open(io.BytesIO(page.content))
            with tempfile.TemporaryDirectory() as tmpdir:
                crops = self._write_crops(image, [line.bbox for line in segmented.value], tmpdir)
                if not crops:
                    return Ok(())
                (Path(tmpdir) / "out").mkdir()
                cmd = [
                    "calamari-predict",
                    "--checkpoint",
                    *checkpoints,
                    "--data.images",
                    *crops,
                    "--extended_prediction_data",
                    "true",
                    "--extended_prediction_data_format",
                    "json",
                    "--output_dir",
                    "out",
                    *self._args,
                ]
                result = subprocess.run(  # nosec B603 - argv list, no shell
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_TIMEOUT_S,
                    cwd=tmpdir,
                )
                if result.returncode != 0:
                    tail = result.stderr.strip()[-500:]
                    return Err(EngineError(f"Calamari failed (exit {result.returncode}): {tail}"))
                return Ok(tuple(self._read_blocks(Path(tmpdir) / "out", crops)))

        except FileNotFoundError:
            return Err(
                EngineError(
                    "calamari-predict not found on PATH — "
                    "install with: pip install omniocr[calamari]"
                )
            )
        except subprocess.TimeoutExpired:
            return Err(EngineError(f"Calamari subprocess timed out after {_TIMEOUT_S}s"))
        except (OSError, ValueError) as exc:
            return Err(EngineError(f"Calamari extraction failed: {exc}"))

    def _checkpoints(self) -> list[str]:
        """Resolve ``model_glob`` to checkpoint paths without extension.

        Matches on the ``.json`` file, not the ``.h5``: Calamari migrates an old
        checkpoint in place on first load (the ``.h5`` is renamed ``.h5_v3`` and
        a new one written), so a ``*.ckpt.h5`` glob stops matching after one run
        while ``*.ckpt.json`` keeps matching.
        """
        base = self._model_glob
        for ext in (".h5", ".json"):
            if base.endswith(ext):
                base = base[: -len(ext)]
        return sorted(
            {os.path.abspath(match[: -len(".json")]) for match in glob.glob(base + ".json")}
        )

    @staticmethod
    def _write_crops(image: Image.Image, boxes: Sequence[BBox], tmpdir: str) -> dict[str, BBox]:
        """Save one PNG per line box; returns file name -> box in reading order."""
        written: dict[str, BBox] = {}
        for index, box in enumerate(boxes):
            right = min(box.x + box.w, image.width)
            bottom = min(box.y + box.h, image.height)
            if right <= box.x or bottom <= box.y:
                continue
            name = f"line_{index:04d}.png"
            image.crop((box.x, box.y, right, bottom)).save(Path(tmpdir) / name)
            written[name] = box
        return written

    def _read_blocks(self, out_dir: Path, crops: dict[str, BBox]) -> list[OCRBlock]:
        """Turn Calamari's per-line JSON files into immutable domain blocks."""
        run = EngineRun(
            engine=self.name,
            model_ref=ModelRef(
                engine=self.name,
                model_name=self._model_glob,
                model_hash="",
                params=self._args,
            ),
            model_hash="",
            params=self._args,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        blocks: list[OCRBlock] = []
        for name, box in crops.items():
            stem = Path(name).stem
            path = out_dir / f"{stem}.json"
            if not path.is_file():
                continue
            predictions = json.loads(path.read_text(encoding="utf-8")).get("predictions", [])
            if not predictions:
                continue
            # With several checkpoints Calamari adds a "voted" entry; use it.
            chosen = next((p for p in predictions if p.get("id") == "voted"), predictions[0])
            text = unicodedata.normalize("NFC", str(chosen.get("sentence", ""))).strip()
            if not text:
                continue
            probability = float(chosen.get("avg_char_probability", 0.0))
            blocks.append(
                OCRBlock(
                    id=f"calamari-{stem}",
                    text=text,
                    confidence=Confidence(max(0.0, min(100.0, probability * 100.0))),
                    bbox=box,
                    provenance=run,
                )
            )
        return blocks


__all__ = ["CalamariEngine"]
