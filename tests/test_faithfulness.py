from __future__ import annotations

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext


def test_post_correction_never_changes_source_text_bytes() -> None:
    source = "α\u0313\u03b9"
    line = OCRLine(
        id="faithful-1",
        text=source,
        confidence=Confidence(80),
        bbox=BBox(0, 0, 20, 10),
        script=Script.ANCIENT,
    )
    before = line.text.encode("utf-8")

    result = SuggestOnlyCorrector().correct(line, TenantContext("o", "u", "d"))

    assert result.is_ok()
    assert line.text.encode("utf-8") == before
    assert all(s.line_id == line.id for s in result.value)


def test_post_correction_flags_a_leading_combining_mark_without_rewriting() -> None:
    line = OCRLine(
        id="faithful-2",
        text="\u0313α",
        confidence=Confidence(80),
        bbox=BBox(0, 0, 20, 10),
        script=Script.ANCIENT,
    )

    result = SuggestOnlyCorrector().correct(line, TenantContext("o", "u", "d"))

    assert result.is_ok()
    assert any(s.reason == "dangling_combining_mark" for s in result.value)
    assert line.text == "\u0313α"
