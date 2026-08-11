from __future__ import annotations

import re
from typing import Sequence

from omniocr.application.structure.geometry import PageGeometry, is_centred
from omniocr.domain.models import OCRLine, ParagraphRole, RegionType


def classify(paragraph_lines: Sequence[OCRLine], page: PageGeometry) -> ParagraphRole:
    """Classify the role of a paragraph based on its lines and page geometry."""
    if not paragraph_lines:
        return ParagraphRole.BODY

    # If any line is marked as RUNNING_HEAD, map to RUNNING_HEAD or PAGE_NUMBER
    for line in paragraph_lines:
        if line.region_type == RegionType.RUNNING_HEAD:
            clean_text = line.text.strip().lower()
            # Standalone digits or Roman numerals are page numbers
            if clean_text.isdigit() or re.match(r"^[ivxlcdm]+$", clean_text):
                return ParagraphRole.PAGE_NUMBER
            return ParagraphRole.RUNNING_HEAD

    # Heading detection heuristic: height >= 1.3 * median_line_height, short, and centred
    first_line = paragraph_lines[0]
    column_width = page.right - page.left
    if column_width > 0:
        # ponytail: bbox height as type-size proxy; read real font metrics if headings misfire
        is_tall = first_line.bbox.h >= 1.3 * page.median_line_height
        is_short = first_line.bbox.w < 0.7 * column_width
        if is_tall and is_short and is_centred(first_line, page.left, page.right):
            return ParagraphRole.HEADING

    return ParagraphRole.BODY
