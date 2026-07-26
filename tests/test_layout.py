from __future__ import annotations

import io
from types import SimpleNamespace

from PIL import Image

from omniocr.application.pipeline import InMemoryPage
from omniocr.domain.models import Script, TenantContext
from omniocr.infrastructure.kraken import KrakenLayoutAnalyzer


def test_kraken_layout_analyzer_preserves_order_and_line_geometry() -> None:
    def segmenter(image):
        return SimpleNamespace(
            lines=(
                SimpleNamespace(boundary=((20, 40), (120, 40), (120, 60), (20, 60))),
                SimpleNamespace(boundary=((10, 10), (100, 10), (100, 30), (10, 30))),
            )
        )

    image_data = io.BytesIO()
    Image.new("L", (200, 100), 255).save(image_data, format="PNG")
    analyzer = KrakenLayoutAnalyzer(Script.POLYTONIC, segmenter=segmenter)
    result = analyzer.segment(
        InMemoryPage(1, image_data.getvalue(), 200, 100), TenantContext("o", "u", "d")
    )

    assert result.is_ok()
    assert [line.id for line in result.value] == ["line-1", "line-2"]
    assert result.value[0].bbox.x == 20
    assert result.value[0].bbox.y == 40
    assert result.value[1].script == Script.POLYTONIC
