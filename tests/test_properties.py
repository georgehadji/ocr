"""Property-based tests for OmniOCR domain invariants.

Uses ``hypothesis`` to verify core invariants that must hold for all
valid inputs — these are the most efficient tests in the pyramid per
BUILD_PLAN §8.1.
"""

from __future__ import annotations

from hypothesis import given, assume
from hypothesis.strategies import text, integers, floats, sampled_from

from omniocr.application.post_correction import SuggestOnlyCorrector
from omniocr.application.metrics import character_error_rate, word_error_rate
from omniocr.domain.models import (
    BBox,
    Confidence,
    OCRLine,
    Script,
    TenantContext,
)


# ---------- Helpers ----------

_ASCII_TEXT = text(max_size=50)
_GREEK_TEXT = text(
    alphabet="αὰἀἁἄἅἆἇβγδεὲἐἑἔἕζηὴἠἡἤἥἦἧθικλμνξοὸὀὁὄὅπρσστυὺὐὑὔὕφχψωὼὠὡὤὥὦὧἰἱἴἵἶἷύ",
    max_size=50,
)
_SCRIPT_STRATEGY = sampled_from(list(Script))


def _line(text: str, script: Script = Script.MODERN) -> OCRLine:
    return OCRLine(
        id="prop-line",
        text=text,
        confidence=Confidence(90),
        bbox=BBox(0, 0, 100, 20),
        script=script,
    )


# ---------- NFC idempotence properties ----------


@given(text=_GREEK_TEXT)
def test_nfc_normalization_is_idempotent(text: str) -> None:
    """NFC-normalized text should not change when normalized again."""
    import unicodedata

    once = unicodedata.normalize("NFC", text)
    twice = unicodedata.normalize("NFC", once)
    assert once == twice


@given(text=_GREEK_TEXT)
def test_suggest_only_corrector_never_mutates_source_text(text: str) -> None:
    """The corrector must never alter the source text — it can only suggest."""
    line = _line(text, Script.POLYTONIC)
    result = SuggestOnlyCorrector().correct(line, TenantContext("o", "u", "d"))

    assert result.is_ok()
    assert line.text == text  # source text unchanged


@given(text=_GREEK_TEXT)
def test_corrector_always_returns_ok(text: str) -> None:
    """The corrector must never fail — it has no failure mode."""
    line = _line(text)
    result = SuggestOnlyCorrector().correct(line, TenantContext("o", "u", "d"))
    assert result.is_ok()


# ---------- CER/WER metric properties ----------


@given(text1=_GREEK_TEXT, text2=_GREEK_TEXT)
def test_character_error_rate_is_non_negative(text1: str, text2: str) -> None:
    """CER and WER must never be negative."""
    assert character_error_rate(text1, text2) >= 0
    assert word_error_rate(text1, text2) >= 0


@given(text=_GREEK_TEXT)
def test_cer_is_zero_for_identical_strings(text: str) -> None:
    """CER must be 0 when comparing a string to itself."""
    assert character_error_rate(text, text) == 0.0
    assert word_error_rate(text, text) == 0.0


@given(text=_GREEK_TEXT)
def test_cer_symmetric_neighbor(text: str) -> None:
    """CER with one character added must be positive (roughly 1/len)."""
    assume(len(text) > 0)
    cer = character_error_rate(text, text + "α")
    assert cer > 0
    assert cer <= 1.0


# ---------- BBox invariant properties ----------


@given(x=integers(0, 1000), y=integers(0, 1000), w=integers(1, 500), h=integers(1, 500))
def test_bbox_right_and_bottom_are_positive(x: int, y: int, w: int, h: int) -> None:
    """BBox.right and BBox.bottom must be strictly greater than x and y."""
    box = BBox(x, y, w, h)
    assert box.right > box.x
    assert box.bottom > box.y


@given(
    x1=integers(0, 500),
    y1=integers(0, 500),
    w1=integers(1, 200),
    h1=integers(1, 200),
    x2=integers(0, 500),
    y2=integers(0, 500),
    w2=integers(1, 200),
    h2=integers(1, 200),
)
def test_boxes_overlap_symmetric(x1, y1, w1, h1, x2, y2, w2, h2) -> None:
    """Bounding box overlap check must be symmetric."""
    from omniocr.application.pipeline import PipelineOrchestrator

    a = BBox(x1, y1, w1, h1)
    b = BBox(x2, y2, w2, h2)
    assert PipelineOrchestrator._boxes_overlap(a, b) == PipelineOrchestrator._boxes_overlap(b, a)


@given(
    x=integers(100, 200),
    y=integers(100, 200),
    w=integers(10, 50),
    h=integers(10, 50),
    offset=integers(300, 500),
)
def test_boxes_do_not_overlap_when_separate(x, y, w, h, offset) -> None:
    """Two bounding boxes far apart must not overlap."""
    from omniocr.application.pipeline import PipelineOrchestrator

    a = BBox(x, y, w, h)
    b = BBox(x + offset, y + offset, w, h)
    assert not PipelineOrchestrator._boxes_overlap(a, b)


# ---------- Confidence invariant properties ----------


@given(value=floats(0, 100))
def test_confidence_accepts_valid_range(value: float) -> None:
    """Confidence values in [0, 100] must be accepted."""
    c = Confidence(value)
    assert c.value == value
