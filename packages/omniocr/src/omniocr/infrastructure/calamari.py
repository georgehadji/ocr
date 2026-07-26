"""Optional Calamari OCR subprocess adapter with GPLv3 isolation.

Per BUILD_PLAN §4.8: Calamari is invoked as a subprocess over a temp
directory. The core package never imports ``calamari_ocr`` directly —
the license isolation CI check enforces this.

Use through the optional ``omniocr[calamari]`` extra, which installs
calamari_ocr in a separate environment or as a subprocess dependency.
"""

from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

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
from omniocr.ports.interfaces import IOCREngine, RawPage


class CalamariEngine(IOCREngine):
    """Subprocess adapter for Calamari OCR.

    Runs ``calamari-predict`` CLI over a temp directory. The caller must
    ensure Calamari is installed and available on ``PATH`` — it is NOT
    imported into the Python process.

    GPLv3 license note: Calamari (``calamari_ocr``) is GPLv3-licensed.
    This adapter isolates it in a subprocess so the core ``omniocr``
    package remains under its permissive license.
    """

    name = "calamari"

    def __init__(
        self,
        model_glob: str = "*.ckpt.h5",
        args: Sequence[str] = (),
    ) -> None:
        self._model_glob = model_glob
        self._args = tuple(args)

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            import glob

            with tempfile.TemporaryDirectory() as tmpdir:
                image_path = Path(tmpdir) / "page.png"
                image_path.write_bytes(page.content)

                cmd = [
                    "calamari-predict",
                    "--files", str(image_path),
                    "--checkpoint", self._model_glob,
                    *self._args,
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    return Err(
                        EngineError(
                            f"Calamari failed (exit {result.returncode}): {result.stderr.strip()}"
                        )
                    )

                blocks = self._parse_output(result.stdout, page.width, page.height)
                return Ok(tuple(blocks))

        except FileNotFoundError:
            return Err(
                EngineError(
                    "calamari-predict not found on PATH — "
                    "install with: pip install omniocr[calamari]"
                )
            )
        except subprocess.TimeoutExpired:
            return Err(EngineError("Calamari subprocess timed out after 120s"))
        except Exception as exc:
            return Err(EngineError(f"Calamari extraction failed: {exc}"))

    def _parse_output(
        self, output: str, page_width: int, page_height: int
    ) -> list[OCRBlock]:
        """Convert Calamari JSON output into immutable domain blocks."""
        import json

        timestamp = datetime.now(timezone.utc).isoformat()
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
            timestamp=timestamp,
        )
        blocks: list[OCRBlock] = []

        try:
            predictions = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return blocks

        if isinstance(predictions, dict):
            predictions = [predictions]

        for pred_index, prediction in enumerate(predictions):
            texts = prediction.get("texts", [])
            for text_index, text in enumerate(texts):
                if not text:
                    continue
                blocks.append(
                    OCRBlock(
                        id=f"calamari-{pred_index}-{text_index}",
                        text=str(text).strip(),
                        confidence=Confidence(0.0),
                        bbox=BBox(0, 0, page_width, page_height),
                        provenance=run,
                    )
                )

        return blocks


__all__ = ["CalamariEngine"]
