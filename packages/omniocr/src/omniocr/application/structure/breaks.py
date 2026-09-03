from __future__ import annotations

from typing import Protocol

from omniocr.application.structure.geometry import PageGeometry
from omniocr.domain.models import OCRLine


class BreakRule(Protocol):
    @property
    def name(self) -> str: ...

    def breaks_before(self, previous: OCRLine, current: OCRLine, page: PageGeometry) -> bool: ...


class IndentRule:
    @property
    def name(self) -> str:
        return "IndentRule"

    def breaks_before(self, previous: OCRLine, current: OCRLine, page: PageGeometry) -> bool:
        column_width = page.right - page.left
        if column_width <= 0:
            return False
        # Indent must be non-trivial compared to previous line to prevent false positives
        is_indented = current.bbox.x > page.left + 0.02 * column_width
        # Also ensure it is indented more than the previous line to differentiate from a block indent
        more_indented_than_prev = current.bbox.x > previous.bbox.x + 0.01 * column_width
        return is_indented and more_indented_than_prev


class ShortLineRule:
    @property
    def name(self) -> str:
        return "ShortLineRule"

    def breaks_before(self, previous: OCRLine, current: OCRLine, page: PageGeometry) -> bool:
        column_width = page.right - page.left
        if column_width <= 0:
            return False
        # If the previous line ended significantly early, it marks a paragraph break
        return previous.bbox.right < page.right - 0.15 * column_width


class GapRule:
    @property
    def name(self) -> str:
        return "GapRule"

    def breaks_before(self, previous: OCRLine, current: OCRLine, page: PageGeometry) -> bool:
        # If there's an unusually large vertical gap, it marks a paragraph break
        gap = current.bbox.y - previous.bbox.bottom
        # Only apply if median_leading is positive and gap is significantly larger
        if page.median_leading > 0:
            return gap > 1.5 * page.median_leading
        return False
