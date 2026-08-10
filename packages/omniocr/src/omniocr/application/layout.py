"""Layout analysis with a second opinion when the primary segmenter gives up."""

from __future__ import annotations

from typing import Sequence

from omniocr.domain.errors import LayoutError
from omniocr.domain.models import OCRLine, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import ILayoutAnalyzer, RawPage


class FallbackLayoutAnalyzer(ILayoutAnalyzer):
    """Try ``primary``; fall back when it yields no lines or errors.

    A segmenter returning zero lines is not a neutral result — it deletes the
    page. Kraken does exactly that on grainy scans: ``kraken.pageseg.segment``
    exceeds its connected-component limit, returns an empty list, and reports
    **no error**, so the failure is indistinguishable from a blank page. On the
    Δεδούσης scans that silently dropped 23 of 74 pages, each holding 127-429
    words that Tesseract reads without trouble.

    Zero lines is therefore treated as failure and retried, not accepted.
    """

    def __init__(self, primary: ILayoutAnalyzer, fallback: ILayoutAnalyzer) -> None:
        self._primary = primary
        self._fallback = fallback

    def segment(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRLine], LayoutError]:
        result = self._primary.segment(page, context)
        if isinstance(result, Ok) and result.value:
            return result
        fallback = self._fallback.segment(page, context)
        if isinstance(fallback, Ok) and fallback.value:
            return fallback
        # Neither found anything. Prefer the primary's error if it had one, so
        # the message names the analyzer that was actually supposed to work.
        if isinstance(result, Err):
            return result
        if isinstance(fallback, Err):
            return fallback
        return Ok(())


__all__ = ["FallbackLayoutAnalyzer"]
