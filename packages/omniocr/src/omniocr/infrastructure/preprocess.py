from __future__ import annotations

import unicodedata
from typing import Any

from omniocr.domain.models import TenantContext
from omniocr.infrastructure.ingest import ImagePage
import io

from omniocr.domain.result import Err, Ok, Result
from omniocr.domain.errors import IngestError
from omniocr.ports.interfaces import IImageProcessor, RawPage


def normalize_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class PassthroughProcessor:
    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Ok(page)


class GrayscaleProcessor:
    """Decode a raster page, convert it to grayscale, and emit PNG bytes."""

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(page.content)).convert("L")
            output = io.BytesIO()
            image.save(output, format="PNG")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=output.getvalue(),
                    width=image.width,
                    height=image.height,
                )
            )
        except Exception as exc:
            return Err(IngestError(f"image preprocessing failed: {exc}"))


class SauvolaProcessor:
    """Apply local adaptive binarization for degraded printed pages.

    OpenCV is imported only when this adapter is used. Kraken/VLM callers should
    generally keep the grayscale page instead of applying this high-contrast filter.
    """

    def __init__(self, window_size: int = 11, constant: float = 2.0) -> None:
        if window_size < 3 or window_size % 2 == 0:
            raise ValueError("window_size must be an odd integer greater than or equal to 3")
        self._window_size = window_size
        self._constant = constant

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("page content is not a decodable raster image")
            binary = cv2.adaptiveThreshold(
                image,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                self._window_size,
                self._constant,
            )
            encoded, content = cv2.imencode(".png", binary)
            if not encoded:
                raise ValueError("OpenCV could not encode the binarized page")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=content.tobytes(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
            )
        except Exception as exc:
            return Err(IngestError(f"adaptive binarization failed: {exc}"))


class OtsuProcessor:
    """Global binarization by Otsu's method — the ensemble's third opinion.

    Not a better Sauvola. It is a *differently wrong* one, which is the whole
    point of ENHANCEMENT_PLAN A3's variant ensembling: the same model on the
    same line, given differently binarized images, makes different errors, and
    A4's merge turns that disagreement into signal.

    Where they differ is predictable. Otsu picks one threshold for the whole
    page from its intensity histogram, so it is clean and fast on evenly lit
    print and fails badly on a page with a shadowed gutter. Sauvola thresholds
    locally, so it survives uneven lighting but invents texture in blank
    margins. On this project's material both happen, on different pages.

    Geometry is preserved exactly — same width, same height, no resampling.
    That is a requirement rather than an incidental property: layout is
    segmented once and every variant's word boxes are matched against those
    segments, so a variant that moved or rescaled pixels would break block
    assignment.
    """

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("page content is not a decodable raster image")
            _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            encoded, content = cv2.imencode(".png", binary)
            if not encoded:
                raise ValueError("OpenCV could not encode the binarized page")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=content.tobytes(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
            )
        except Exception as exc:
            return Err(IngestError(f"Otsu binarization failed: {exc}"))


class DeskewProcessor:
    """Rotate a page so its text baselines are horizontal.

    The highest-value preprocessing step for this project: Kraken's line
    segmentation degrades sharply past roughly a degree of skew, and a scanned
    book routinely arrives at 0.5-3 degrees. Nothing downstream can recover a
    line the segmenter failed to find.

    Detection maximizes the variance of the horizontal projection profile. On
    a straight page every text row projects into a tall spike and every gap
    into a trough, so variance peaks; on a skewed page rows smear across rows
    and flatten it. Chosen over ``cv2.minAreaRect`` over the foreground, which
    is shorter but measures the bounding box of *everything* on the page — a
    plate, a rule, or a marginal stamp drags the estimate off the text it is
    supposed to be measuring.

    Search is coarse-to-fine on a downscaled copy: the profile is a row-sum,
    so full resolution buys nothing but time.
    """

    def __init__(self, max_angle: float = 5.0, fine_step: float = 0.1) -> None:
        if max_angle <= 0:
            raise ValueError("max_angle must be positive")
        if fine_step <= 0:
            raise ValueError("fine_step must be positive")
        self._max_angle = max_angle
        self._fine_step = fine_step

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("page content is not a decodable raster image")

            angle = self._estimate_angle(image)
            # Sub-tenth-of-a-degree rotation costs an interpolation pass over
            # every pixel and cannot help a segmenter. Leave the page alone.
            if abs(angle) < self._fine_step:
                return Ok(page)

            height, width = image.shape
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
            # borderValue=255: the corners exposed by rotation must read as
            # paper. Filled with the default 0 they are black, and an adaptive
            # binarizer downstream treats that as ink.
            rotated = cv2.warpAffine(
                image,
                matrix,
                (width, height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=255,
            )
            encoded, content = cv2.imencode(".png", rotated)
            if not encoded:
                raise ValueError("OpenCV could not encode the deskewed page")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=content.tobytes(),
                    width=int(width),
                    height=int(height),
                )
            )
        except Exception as exc:
            return Err(IngestError(f"deskew failed: {exc}"))

    def _estimate_angle(self, image: Any) -> float:
        """Return the rotation in degrees that best levels the text rows."""
        import cv2

        # ponytail: fixed 1000px working width. Enough rows survive to score a
        # profile on any book page; make it adaptive only if a real page misses.
        scale = 1000.0 / max(int(image.shape[1]), 1)
        work = image
        if scale < 1.0:
            work = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        # Ink as 1, paper as 0, so a row-sum counts ink per row.
        binary = cv2.threshold(work, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

        best = 0.0
        for step in (0.5, self._fine_step):
            span = self._max_angle if step == 0.5 else 0.5
            offset = -span
            candidates: list[float] = []
            while offset <= span + step / 2:
                candidates.append(best + offset)
                offset += step
            best = max(candidates, key=lambda a: self._profile_score(binary, a))
        return best

    @staticmethod
    def _profile_score(binary: Any, angle: float) -> float:
        import cv2

        height, width = int(binary.shape[0]), int(binary.shape[1])
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        rotated = cv2.warpAffine(
            binary, matrix, (width, height), flags=cv2.INTER_NEAREST, borderValue=0
        )
        return float(rotated.sum(axis=1, dtype="float64").var())


class DespeckleProcessor:
    """Drop connected components too small to be any printed mark.

    Scanner speckle becomes phantom diacritics, which is a specifically Greek
    failure: a stray dot above a vowel is a plausible tonos, so the recognizer
    emits one and the result is wrong in a way that reads as correct.

    ``min_area`` is deliberately tiny. At the 300 DPI this project renders at,
    a tonos is roughly 8x8px (~64px) and the dot of an iota dialytika is not
    much smaller, so anything near those sizes must survive. The default of 6
    removes only isolated specks of a few pixels. Raising it past ~20 starts
    eating real diacritics — the exact damage this step exists to prevent.
    """

    def __init__(self, min_area: int = 6) -> None:
        if min_area < 1:
            raise ValueError("min_area must be positive")
        self._min_area = min_area

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError("page content is not a decodable raster image")

            binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
            count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

            cleaned = image.copy()
            for label in range(1, count):  # 0 is the background
                if stats[label, cv2.CC_STAT_AREA] < self._min_area:
                    cleaned[labels == label] = 255

            encoded, content = cv2.imencode(".png", cleaned)
            if not encoded:
                raise ValueError("OpenCV could not encode the despeckled page")
            return Ok(
                ImagePage(
                    number=page.number,
                    content=content.tobytes(),
                    width=int(image.shape[1]),
                    height=int(image.shape[0]),
                )
            )
        except Exception as exc:
            return Err(IngestError(f"despeckle failed: {exc}"))


class ChainProcessor:
    """Run processors in order, stopping at the first failure.

    Preprocessing is a pipeline of independent transforms, but the orchestrator
    accepts a single ``IImageProcessor``. This composes them without teaching it
    about lists, so a composition root stays the only place that decides which
    steps a given edition runs.
    """

    def __init__(self, *processors: IImageProcessor) -> None:
        if not processors:
            raise ValueError("ChainProcessor needs at least one processor")
        self._processors = processors

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        current = page
        for processor in self._processors:
            result = processor.process(current, context)
            if isinstance(result, Err):
                return result
            current = result.value
        return Ok(current)


__all__ = [
    "ChainProcessor",
    "DespeckleProcessor",
    "DeskewProcessor",
    "GrayscaleProcessor",
    "PassthroughProcessor",
    "SauvolaProcessor",
    "normalize_nfc",
]
