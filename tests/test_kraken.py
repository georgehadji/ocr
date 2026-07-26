from types import SimpleNamespace

import pytest

from omniocr.infrastructure.kraken import KrakenEngine


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
