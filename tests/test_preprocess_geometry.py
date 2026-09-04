"""Tests for the deskew/despeckle/chain preprocessing stages.

The despeckle tests matter more than the deskew ones. Deskew failing is
visible — lines get missed and the page comes back short. Despeckle failing is
invisible: it removes a tonos, the recognizer reads an unaccented vowel, and
the output looks like ordinary text while being wrong. So the size floor gets
tested against a real diacritic, not just against a speck.
"""

from __future__ import annotations


import pytest

from omniocr.domain.models import TenantContext
from omniocr.domain.result import Ok
from omniocr.infrastructure.ingest import ImagePage
from omniocr.infrastructure.preprocess import (
    ChainProcessor,
    DespeckleProcessor,
    DeskewProcessor,
    GrayscaleProcessor,
)

cv2 = pytest.importorskip("cv2", reason="preprocessing geometry needs the opencv extra")
np = pytest.importorskip("numpy")


CONTEXT = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")


def _page(array: "np.ndarray") -> ImagePage:
    encoded, buffer = cv2.imencode(".png", array)
    assert encoded
    return ImagePage(
        number=1,
        content=buffer.tobytes(),
        width=int(array.shape[1]),
        height=int(array.shape[0]),
    )


def _decode(page: ImagePage) -> "np.ndarray":
    decoded = cv2.imdecode(np.frombuffer(page.content, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded is not None
    return decoded


def _text_page(rows: int = 14, angle: float = 0.0) -> "np.ndarray":
    """A synthetic page of evenly spaced dark text rows, optionally rotated."""
    page = np.full((600, 800), 255, dtype=np.uint8)
    for index in range(rows):
        top = 40 + index * 38
        page[top : top + 14, 80:720] = 0
    if angle:
        matrix = cv2.getRotationMatrix2D((400.0, 300.0), angle, 1.0)
        page = cv2.warpAffine(page, matrix, (800, 600), flags=cv2.INTER_CUBIC, borderValue=255)
    return page


class TestDeskew:
    @pytest.mark.parametrize("angle", [-2.5, -1.0, 1.0, 2.5])
    def test_recovers_a_known_skew(self, angle: float) -> None:
        """The estimate must land within a tenth of a degree of the truth."""
        processor = DeskewProcessor()
        skewed = _text_page(angle=angle)
        estimated = processor._estimate_angle(skewed)
        # The page was rotated by `angle`, so levelling it needs -angle.
        assert estimated == pytest.approx(-angle, abs=0.2)

    def test_straight_page_is_returned_untouched(self) -> None:
        """No rotation means no interpolation pass — the bytes must be identical."""
        processor = DeskewProcessor()
        page = _page(_text_page(angle=0.0))
        result = processor.process(page, CONTEXT)
        assert isinstance(result, Ok)
        assert result.value.content == page.content

    def test_exposed_corners_are_paper_not_ink(self) -> None:
        """Rotation fills the corners; filled black they read as ink downstream."""
        processor = DeskewProcessor()
        result = processor.process(_page(_text_page(angle=3.0)), CONTEXT)
        assert isinstance(result, Ok)
        corner = _decode(result.value)[0, 0]
        assert corner > 200, "rotation exposed a dark corner"

    def test_undecodable_content_is_an_error_not_a_crash(self) -> None:
        processor = DeskewProcessor()
        result = processor.process(ImagePage(1, b"not an image", 10, 10), CONTEXT)
        assert not isinstance(result, Ok)

    def test_rejects_nonsense_configuration(self) -> None:
        with pytest.raises(ValueError):
            DeskewProcessor(max_angle=0)
        with pytest.raises(ValueError):
            DeskewProcessor(fine_step=0)


class TestDespeckle:
    def test_removes_isolated_specks(self) -> None:
        page = np.full((200, 200), 255, dtype=np.uint8)
        page[100:120, 40:160] = 0  # a text-sized bar
        page[20, 20] = 0  # a single-pixel speck
        page[30, 150] = 0

        result = DespeckleProcessor().process(_page(page), CONTEXT)
        assert isinstance(result, Ok)
        cleaned = _decode(result.value)
        assert cleaned[20, 20] == 255
        assert cleaned[30, 150] == 255
        assert cleaned[110, 100] == 0, "the text bar must survive"

    @pytest.mark.parametrize("size", [3, 4, 6, 8])
    def test_a_tonos_sized_mark_survives(self, size: int) -> None:
        """The failure this stage must never cause.

        At 300 DPI a tonos is roughly 8x8px. A despeckler that eats one turns
        ά into α — wrong output that reads as correct. Anything at or above the
        smallest plausible diacritic has to survive the default threshold.
        """
        page = np.full((200, 200), 255, dtype=np.uint8)
        page[100:120, 40:160] = 0
        page[50 : 50 + size, 100 : 100 + size] = 0  # the diacritic

        result = DespeckleProcessor().process(_page(page), CONTEXT)
        assert isinstance(result, Ok)
        cleaned = _decode(result.value)
        assert cleaned[50, 100] == 0, f"a {size}x{size} diacritic was destroyed"

    def test_rejects_nonsense_configuration(self) -> None:
        with pytest.raises(ValueError):
            DespeckleProcessor(min_area=0)


class TestChain:
    def test_runs_every_stage_in_order(self) -> None:
        chain = ChainProcessor(GrayscaleProcessor(), DeskewProcessor(), DespeckleProcessor())
        result = chain.process(_page(_text_page(angle=1.5)), CONTEXT)
        assert isinstance(result, Ok)
        assert result.value.width == 800

    def test_stops_at_the_first_failure(self) -> None:
        """A later stage must not run on a page an earlier one rejected."""

        class _Exploding:
            def process(self, page: object, context: object) -> object:
                raise AssertionError("must not be reached")

        chain = ChainProcessor(DeskewProcessor(), _Exploding())  # type: ignore[arg-type]
        result = chain.process(ImagePage(1, b"not an image", 10, 10), CONTEXT)
        assert not isinstance(result, Ok)

    def test_rejects_an_empty_chain(self) -> None:
        with pytest.raises(ValueError):
            ChainProcessor()
