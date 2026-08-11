from __future__ import annotations

import collections
import statistics
from dataclasses import dataclass
from typing import Sequence

from omniocr.domain.models import OCRLine


@dataclass(frozen=True, slots=True)
class PageGeometry:
    left: int
    right: int
    median_leading: float
    median_line_height: float
    width: int
    height: int


def _bucketed_mode(values: Sequence[int], bucket_size: int = 5) -> int:
    if not values:
        return 0
    bucketed = [v // bucket_size * bucket_size for v in values]
    counter = collections.Counter(bucketed)
    mode_bucket = counter.most_common(1)[0][0]
    matching_values = [v for v in values if v // bucket_size * bucket_size == mode_bucket]
    return int(statistics.median(matching_values))


def column_left_edge(lines: Sequence[OCRLine]) -> int:
    return _bucketed_mode([line.bbox.x for line in lines])


def column_right_edge(lines: Sequence[OCRLine]) -> int:
    return _bucketed_mode([line.bbox.right for line in lines])


def median_leading(lines: Sequence[OCRLine]) -> float:
    if len(lines) < 2:
        return 0.0
    gaps = [lines[i].bbox.y - lines[i - 1].bbox.bottom for i in range(1, len(lines))]
    return float(statistics.median(gaps))


def median_line_height(lines: Sequence[OCRLine]) -> float:
    if not lines:
        return 0.0
    return float(statistics.median(line.bbox.h for line in lines))


def is_centred(line: OCRLine, left: int, right: int, tolerance: float = 15.0) -> bool:
    left_indent = line.bbox.x - left
    right_indent = right - line.bbox.right
    # A centered line must not be full width (i.e. it must have non-trivial indents)
    column_width = right - left
    if column_width <= 0:
        return False
    if line.bbox.w > 0.85 * column_width:
        return False
    return abs(left_indent - right_indent) <= tolerance
