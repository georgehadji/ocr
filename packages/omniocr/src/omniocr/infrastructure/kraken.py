from __future__ import annotations

import hashlib
import io
import logging
import threading
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
from omniocr.infrastructure.device import CPU, is_accelerator, select_device
from omniocr.ports.interfaces import ILayoutAnalyzer, IOCREngine, RawPage

_LOG = logging.getLogger("omniocr.kraken")


def _open_bilevel(content: bytes) -> Any:
    """Decode page bytes into the bi-level image ``kraken.pageseg`` requires.

    Both Kraken entry points must go through this. They previously binarized
    independently and drifted: the analyzer converted, the recognizer did not,
    so recognition died on the grayscale page the preprocessor emits with
    "Image is not bi-level" while segmentation looked healthy.

    ponytail: fixed 128 threshold. Kraken's baseline segmenter (``blla``)
    takes grayscale directly and handles skewed historical lines better, but
    measured >6 GB peak allocation on an 800x180 page — unusable on a CPU-only
    target. Revisit when blla can be run within a memory budget.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(content))
    if image.mode == "1":
        return image
    return image.convert("L").point(lambda value: 255 if value > 128 else 0, mode="1")


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
            segmenter = self._segmenter
            if segmenter is None:
                from kraken.pageseg import segment as kraken_segment

                segmenter = kraken_segment

            segmentation = segmenter(_open_bilevel(page.content))
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
        except ImportError:
            return Err(
                LayoutError(
                    "Kraken layout segmentation requires the optional kraken extra — "
                    "install with: pip install omniocr[kraken]"
                )
            )
        except Exception as exc:
            return Err(LayoutError(f"Kraken layout segmentation failed: {exc}"))

    @staticmethod
    def _bounds(record: Any, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, w, h)`` for one segmentation record.

        Kraken emits two record shapes and both must be handled:

        - ``BBoxLine`` (``pageseg``) carries ``bbox = [x0, y0, x1, y1]``
        - ``BaselineLine`` (``blla``) carries a ``boundary`` polygon

        Raising on an unrecognised shape is deliberate. The previous fallback
        to a full-page box was silent and catastrophic: every line then covered
        the whole page, so every recognized word overlapped every line and each
        line came back holding the entire page's text. ``segment()`` converts
        this into ``Err(LayoutError)``, which is visible; a wrong answer is not.
        """
        bbox = getattr(record, "bbox", None)
        if bbox is None and isinstance(record, dict):
            bbox = record.get("bbox")
        if bbox is not None:
            values = [int(value) for value in bbox]
            if len(values) == 4:
                x0, y0, x1, y1 = values
                return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

        # ``line`` is the polygon a baseline recognition record carries. It is
        # checked last: ``BBoxOCRRecord`` defines it as ``None`` while holding
        # a real ``bbox``, so preferring it would reintroduce the empty-geometry
        # path this method exists to reject.
        geometry = (
            getattr(record, "boundary", None)
            or getattr(record, "polygon", None)
            or getattr(record, "line", None)
        )
        if geometry is None and isinstance(record, dict):
            geometry = record.get("boundary") or record.get("polygon") or record.get("line")
        if geometry:
            points = list(geometry)
            if points and isinstance(points[0], (tuple, list)):
                xs = [int(point[0]) for point in points]
                ys = [int(point[1]) for point in points]
                x, y = min(xs), min(ys)
                return x, y, max(1, max(xs) - x), max(1, max(ys) - y)

        raise LayoutError(
            f"unrecognized Kraken segmentation record: {type(record).__name__} "
            "exposes neither 'bbox' nor a 'boundary'/'polygon' geometry"
        )

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

    def __init__(self, model_path: str | Path, device: str | None = None) -> None:
        self.model_path = Path(model_path)
        self._requested_device = device
        # Resolved on first use, not here: probing CUDA imports torch, and
        # composition roots construct this engine eagerly whether or not a
        # page is ever recognized.
        self._device: str | None = None
        self._model: Any = None
        # One engine instance is shared across the page-parallel workers of
        # ADR-003, and both lazy fields below are written on first use. The
        # lock keeps that to a single load instead of one per racing worker.
        #
        # Reentrant on purpose: _load_model() runs under this lock and reads
        # the `device` property, which locks again. A plain Lock deadlocks the
        # whole pipeline on the very first page.
        #
        # ponytail: one lock for the whole engine — recognition itself is the
        # bottleneck at ~500s/page, so contention on a one-time load is noise.
        # Split it only if profiling ever says otherwise.
        self._lock = threading.RLock()
        self._model_hash = self._hash_model()

    @property
    def device(self) -> str:
        """The torch device in use — an accelerator when available, else ``cpu``."""
        if self._device is None:
            with self._lock:
                if self._device is None:
                    self._device = select_device(self._requested_device)
        return self._device

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            from kraken import pageseg

            image = _open_bilevel(page.content)
            segmentation = pageseg.segment(image)
            return Ok(self._recognize(image, segmentation, page))
        except ImportError:
            return Err(
                EngineError(
                    "Kraken recognition requires the optional kraken extra — "
                    "install with: pip install omniocr[kraken]"
                )
            )
        except Exception as exc:
            return Err(EngineError(f"Kraken extraction failed: {exc}"))

    def _recognize(self, image: Any, segmentation: Any, page: RawPage) -> tuple[OCRBlock, ...]:
        """Recognize one page, degrading from GPU to CPU rather than failing.

        ``rpred`` returns a generator, so GPU errors surface while parsing,
        not at the call. The fallback is permanent for this engine instance:
        a GPU that just OOM'd on one page will OOM on the next, and retrying
        every page would cost a wasted GPU attempt each time.
        """
        from kraken import rpred

        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load_model()
        try:
            return self.parse_records(
                rpred.rpred(self._model, image, segmentation), page.width, page.height
            )
        except Exception as exc:
            if not is_accelerator(self.device):
                raise
            _LOG.warning(
                "Kraken GPU recognition failed on page %s (%s) — falling back to CPU",
                page.number,
                exc,
            )
            with self._lock:
                self._device = CPU
                self._model = self._load_model()
            return self.parse_records(
                rpred.rpred(self._model, image, segmentation), page.width, page.height
            )

    def _load_model(self) -> Any:
        """Load the recognition model, degrading to CPU if the GPU cannot take it."""
        from kraken.lib import models

        try:
            return models.load_any(str(self.model_path), device=self.device)
        except Exception as exc:
            if not is_accelerator(self.device):
                raise
            _LOG.warning("Kraken model load on %s failed (%s) — using CPU", self.device, exc)
            self._device = CPU
            return models.load_any(str(self.model_path), device=CPU)

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
            params=(self.device,),
            timestamp=timestamp,
        )
        blocks: list[OCRBlock] = []
        for index, record in enumerate(records):
            text = str(getattr(record, "prediction", "")).strip()
            if not text:
                continue
            x, y, right, bottom = self._bounds(record, page_width, page_height)
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
    def _bounds(record: Any, page_width: int, page_height: int) -> tuple[int, int, int, int]:
        """Return ``(x, y, right, bottom)`` for one recognition record.

        Delegates to the analyzer's geometry reader so the two Kraken entry
        points cannot drift — the same reason ``_open_bilevel`` is shared.

        They had drifted. The analyzer was fixed to read ``BBoxLine.bbox`` and
        to raise rather than assume a full page; this method was not, and it
        read ``record.line``, which Kraken 7's ``rpred`` sets to ``None`` on
        every ``BBoxOCRRecord``. Both fallbacks here therefore fired for every
        line on every page, stamping all 53 lines of a page with the identical
        full-page box. Recognition was correct and the geometry was a
        fabrication, so the whole page collapsed onto whichever text line it
        happened to overlap most.
        """
        x, y, width, height = KrakenLayoutAnalyzer._bounds(record, page_width, page_height)
        return x, y, x + width, y + height

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
