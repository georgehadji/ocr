from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import BBox, Confidence, EngineRun, ModelRef, OCRBlock, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import IOCREngine, RawPage


class TesseractEngine(IOCREngine):
    """Optional PyTesseract adapter.

    Imports for Pillow and pytesseract are intentionally lazy so the core package
    remains importable when the optional ``tesseract`` extra is not installed.
    """

    name = "tesseract"

    def __init__(self, language: str = "eng", config: str = "") -> None:
        self.language = language
        self.config = config

    def extract(self, page: RawPage, context: TenantContext) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            from PIL import Image
            import pytesseract

            image = Image.open(io.BytesIO(page.content))
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config=self.config,
                output_type=pytesseract.Output.DICT,
            )
            return Ok(self.parse_output(data))
        except Exception as exc:
            return Err(EngineError(f"Tesseract extraction failed: {exc}"))

    def parse_output(self, data: Mapping[str, Sequence[Any]]) -> tuple[OCRBlock, ...]:
        """Convert Tesseract's dictionary output into immutable domain blocks."""
        levels = data.get("level", ())
        timestamp = datetime.now(timezone.utc).isoformat()
        run = EngineRun(
            engine=self.name,
            model_ref=ModelRef(
                engine=self.name,
                model_name=self.language,
                model_hash="unknown",
                params=(self.config,),
            ),
            model_hash="unknown",
            params=(self.config,),
            timestamp=timestamp,
        )
        blocks: list[OCRBlock] = []
        for index, level in enumerate(levels):
            text = str(data.get("text", ("",) * len(levels))[index]).strip()
            if level != 5 or not text:
                continue
            try:
                confidence = float(data.get("conf", ("-1",) * len(levels))[index])
                x = int(data.get("left", (0,) * len(levels))[index])
                y = int(data.get("top", (0,) * len(levels))[index])
                width = int(data.get("width", (0,) * len(levels))[index])
                height = int(data.get("height", (0,) * len(levels))[index])
                if confidence < 0 or confidence > 100 or width <= 0 or height <= 0:
                    continue
                blocks.append(
                    OCRBlock(
                        id=f"word-{index}",
                        text=text,
                        confidence=Confidence(confidence),
                        bbox=BBox(x=x, y=y, w=width, h=height),
                        provenance=run,
                    )
                )
            except (TypeError, ValueError, IndexError):
                continue
        return tuple(blocks)


__all__ = ["TesseractEngine"]
