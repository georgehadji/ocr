from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any, Iterable, Sequence

from omniocr.domain.errors import EngineError, LayoutError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    OCRLine,
    RegionType,
    Script,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ILayoutAnalyzer, IOCREngine, RawPage


class KrakenLayoutAnalyzer(ILayoutAnalyzer):
    """Convert Kraken's ordered line segmentation into immutable OCR lines."""

    def __init__(
        self,
        script: Script = Script.UNKNOWN,
        segmenter: Callable[[Any], Any] | None = None,
    ) -> None:
        self._script = script
        self._segmenter = segmenter

    def segment(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRLine], LayoutError]:
        try:
            from PIL import Image

            segmenter = self._segmenter
            if segmenter is None:
                from kraken.pageseg import segment as kraken_segment

                segmenter = kraken_segment

            image = Image.open(io.BytesIO(page.content))
            segmentation = segmenter(image)
            records = getattr(segmentation, "lines", segmentation)
            lines = tuple(
                OCRLine(
                    id=f"line-{index + 1}",
                    text="",
                    confidence=Confidence(0.0),
                    bbox=BBox(*self._bounds(record, page.width, page.height)),
                    script=self._script,
                    region_type=self._region_type(record),
                    reading_order=index + 1,
                )
                for index, record in enumerate(records)
            )
            return Ok(lines)
        except Exception as exc:
            return Err(LayoutError(f"Kraken layout segmentation failed: {exc}"))

    @staticmethod
    def _bounds(record: Any, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        geometry = getattr(record, "boundary", None) or getattr(record, "polygon", None)
        if geometry is None and isinstance(record, dict):
            geometry = record.get("boundary") or record.get("polygon")
        if geometry:
            points = list(geometry)
            if points and isinstance(points[0], (tuple, list)):
                xs = [int(point[0]) for point in points]
                ys = [int(point[1]) for point in points]
                x, y = min(xs), min(ys)
                return x, y, max(1, max(xs) - x), max(1, max(ys) - y)
        return 0, 0, max(1, page_width), max(1, page_height)

    @staticmethod
    def _region_type(record: Any) -> RegionType:
        """Map a Kraken record's category attribute to a RegionType value."""
        raw = getattr(record, "category", None)
        if raw is None and isinstance(record, dict):
            raw = record.get("category")
        if raw is None:
            return RegionType.UNKNOWN
        try:
            return RegionType(raw.lower().replace("-", "_"))
        except (ValueError, AttributeError):
            return RegionType.UNKNOWN


class KrakenEngine(IOCREngine):
    """Optional Kraken recognition adapter for printed historical material."""

    name = "kraken"

    def __init__(self, model_path: str | Path) -> None:
        self.model_path = Path(model_path)
        self._model: Any = None
        self._model_hash = self._hash_model()

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            from PIL import Image
            from kraken import pageseg, rpred
            from kraken.lib import models

            if self._model is None:
                self._model = models.load_any(str(self.model_path))
            image = Image.open(io.BytesIO(page.content))
            segmentation = pageseg.segment(image)
            records = rpred.rpred(self._model, image, segmentation)
            return Ok(self.parse_records(records, page.width, page.height))
        except Exception as exc:
            return Err(EngineError(f"Kraken extraction failed: {exc}"))

    def parse_records(
        self, records: Iterable[Any], page_width: int, page_height: int
    ) -> tuple[OCRBlock, ...]:
        timestamp = datetime.now(timezone.utc).isoformat()
        run = EngineRun(
            engine=self.name,
            model_ref=ModelRef(
                engine=self.name,
                model_name=self.model_path.name,
                model_hash=self._model_hash,
            ),
            model_hash=self._model_hash,
            params=("cpu",),
            timestamp=timestamp,
        )
        blocks: list[OCRBlock] = []
        for index, record in enumerate(records):
            text = str(getattr(record, "prediction", "")).strip()
            if not text:
                continue
            x, y, right, bottom = self._bounds(
                getattr(record, "line", None), page_width, page_height
            )
            values = [float(value) for value in getattr(record, "confidences", ())]
            confidence = sum(values) / len(values) * 100 if values else 0.0
            blocks.append(
                OCRBlock(
                    id=f"line-{index}",
                    text=text,
                    confidence=Confidence(max(0.0, min(100.0, confidence))),
                    bbox=BBox(x=x, y=y, w=max(1, right - x), h=max(1, bottom - y)),
                    provenance=run,
                )
            )
        return tuple(blocks)

    @staticmethod
    def _bounds(line: Any, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        if not line:
            return 0, 0, page_width, page_height
        points = list(line)
        if points and isinstance(points[0], (tuple, list)):
            xs = [int(point[0]) for point in points]
            ys = [int(point[1]) for point in points]
            return min(xs), min(ys), max(xs), max(ys)
        return 0, 0, page_width, page_height

    def _hash_model(self) -> str:
        try:
            digest = hashlib.sha256()
            with self.model_path.open("rb") as model:
                for chunk in iter(lambda: model.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return "unavailable"


__all__ = ["KrakenEngine", "KrakenLayoutAnalyzer"]
