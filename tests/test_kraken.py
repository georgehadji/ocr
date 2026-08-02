from types import SimpleNamespace

import pytest

from omniocr.domain.errors import LayoutError
from omniocr.infrastructure.kraken import KrakenEngine, KrakenLayoutAnalyzer


def test_kraken_records_become_provenanced_blocks() -> None:
    engine = KrakenEngine("missing-greek.mlmodel")
    blocks = engine.parse_records(
        [
            SimpleNamespace(
                prediction="πολυτονικό",
                confidences=[0.9, 0.8],
                line=[(2, 3), (42, 3), (42, 15), (2, 15)],
            )
        ],
        100,
        100,
    )

    assert blocks[0].text == "πολυτονικό"
    assert blocks[0].confidence.value == pytest.approx(85.0)
    assert blocks[0].bbox.right == 42
    assert blocks[0].provenance is not None
    assert blocks[0].provenance.model_hash == engine._model_hash


def test_bounds_reads_bbox_line_records() -> None:
    """Kraken 6 ``pageseg`` emits ``BBoxLine(bbox=[x0, y0, x1, y1])``.

    Regression: these were previously unhandled and fell through to a
    full-page box, which made every line cover the whole page.
    """
    record = SimpleNamespace(bbox=[31, 36, 723, 62])

    assert KrakenLayoutAnalyzer._bounds(record, 800, 180) == (31, 36, 692, 26)


def test_bounds_reads_baseline_polygon_records() -> None:
    """``blla`` emits ``BaselineLine`` with a ``boundary`` polygon."""
    record = SimpleNamespace(boundary=[(10, 20), (110, 20), (110, 50), (10, 50)])

    assert KrakenLayoutAnalyzer._bounds(record, 800, 180) == (10, 20, 100, 30)


def test_bounds_rejects_unrecognized_record_instead_of_guessing() -> None:
    """An unknown record shape must fail loudly, never default to the page box.

    A full-page fallback is silently catastrophic: every line then overlaps
    every recognized word, so each line comes back holding the whole page.
    """
    with pytest.raises(LayoutError, match="unrecognized Kraken segmentation record"):
        KrakenLayoutAnalyzer._bounds(SimpleNamespace(), 800, 180)


def test_segment_converts_bad_records_into_err() -> None:
    """The loud failure surfaces as ``Err(LayoutError)``, not an exception."""
    import io

    from PIL import Image

    from omniocr.domain.models import TenantContext

    buffer = io.BytesIO()
    Image.new("L", (80, 40), color=255).save(buffer, format="PNG")
    page = SimpleNamespace(number=1, content=buffer.getvalue(), width=80, height=40)
    analyzer = KrakenLayoutAnalyzer(segmenter=lambda image: [SimpleNamespace()])

    result = analyzer.segment(
        page, TenantContext(organization_id="t", user_id="u", subscription_tier="desktop")
    )

    assert result.is_err()
    assert "unrecognized Kraken segmentation record" in str(result.error)
