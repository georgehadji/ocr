from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import BBox, Confidence, EngineRun, ModelRef, OCRBlock, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.infrastructure.config import DEFAULT_TESSERACT_LANGUAGE
from omniocr.infrastructure.models import sha256_file
from omniocr.domain.errors import LayoutError
from omniocr.domain.models import OCRLine, Script
from omniocr.ports.interfaces import ILayoutAnalyzer, IOCREngine, RawPage

# Tesseract's page-hierarchy level for a text line in image_to_data output.
_LINE_LEVEL = 4


class TesseractEngine(IOCREngine):
    """Optional PyTesseract adapter.

    Imports for Pillow and pytesseract are intentionally lazy so the core package
    remains importable when the optional ``tesseract`` extra is not installed.

    When ``model_path`` is given, the engine hashes the traineddata file at init
    and embeds the digest in every ``EngineRun`` for reproducibility (§1.6).
    Desktop callers that lack a known path should leave it ``None`` — the hash
    will be recorded as ``"unknown"``, which is acceptable for development.
    """

    name = "tesseract"

    def __init__(
        self,
        language: str = DEFAULT_TESSERACT_LANGUAGE,
        config: str = "",
        model_path: str | Path | None = None,
    ) -> None:
        self.language = language
        self.config = config
        self._model_hash: str = "unknown"
        if model_path is not None:
            resolved = Path(model_path)
            if resolved.is_file():
                self._model_hash = sha256_file(resolved)

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
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
                model_hash=self._model_hash,
                params=(self.config,),
            ),
            model_hash=self._model_hash,
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


class TesseractLayoutAnalyzer(ILayoutAnalyzer):
    """Line segmentation from Tesseract's own page analysis.

    Exists as a fallback for pages Kraken's segmenter refuses. On grainy
    scans ``kraken.pageseg.segment`` hits its connected-component ceiling
    ("Too many connected components for a page image: 16761"), returns an
    empty line list, and reports no error — so the page silently produces no
    text. Tesseract reads those same pages without complaint.

    Only boxes are taken here. Recognition still runs through the normal
    engine bank, so a fallback page is not quietly downgraded to
    Tesseract-only output.
    """

    def __init__(
        self, language: str = DEFAULT_TESSERACT_LANGUAGE, script: Script = Script.UNKNOWN
    ) -> None:
        self._language = language
        self._script = script

    def segment(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRLine], LayoutError]:
        try:
            from PIL import Image
            import pytesseract

            image = Image.open(io.BytesIO(page.content))
            data = pytesseract.image_to_data(
                image,
                lang=self._language,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:
            return Err(LayoutError(f"Tesseract layout segmentation failed: {exc}"))

        lines: list[OCRLine] = []
        for index in range(len(data.get("level", ()))):
            if int(data["level"][index]) != _LINE_LEVEL:
                continue
            width, height = int(data["width"][index]), int(data["height"][index])
            if width <= 0 or height <= 0:
                continue
            lines.append(
                OCRLine(
                    id=f"line-{len(lines) + 1}",
                    text="",
                    confidence=Confidence(0.0),
                    bbox=BBox(int(data["left"][index]), int(data["top"][index]), width, height),
                    script=self._script,
                    reading_order=len(lines) + 1,
                )
            )
        return Ok(tuple(lines))


__all__ = ["TesseractEngine", "TesseractLayoutAnalyzer"]
