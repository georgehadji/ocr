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


def is_probably_multi_column(
    lines: Sequence[OCRLine], page_width: int, bucket_size: int = 5
) -> bool:
    """True when line left-edges cluster into two well-separated groups.

    Every break rule and role heuristic in this package assumes one column:
    left edge is *the* left margin, and reading order follows line order top
    to bottom. A two-column page violates both, so it must be detected and
    skipped rather than assembled — a wrong guess here interleaves columns
    into nonsense paragraphs, whereas skipping just leaves the page as
    unassembled lines (see docs/STRUCTURE_IMPLEMENTATION_PLAN.md §9).

    Deliberately conservative: both clusters must be substantial (not a
    stray marginal note) and far apart (not indentation noise), so a false
    positive costs a missed paragraph reconstruction, never a false join.
    """
    if len(lines) < 4 or page_width <= 0:
        return False
    bucketed = [line.bbox.x // bucket_size * bucket_size for line in lines]
    counts = collections.Counter(bucketed)
    top_two = counts.most_common(2)
    if len(top_two) < 2:
        return False
    (first_x, first_n), (second_x, second_n) = top_two
    both_substantial = min(first_n, second_n) >= 0.2 * len(lines)
    far_apart = abs(first_x - second_x) > 0.25 * page_width
    return both_substantial and far_apart


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
