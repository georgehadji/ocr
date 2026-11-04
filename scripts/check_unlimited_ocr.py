"""Score ``UnlimitedOCREngine`` end to end against a live endpoint.

Drives the real adapter (request, downscale, parse) over HTTP for each
polytonic scan fixture and scores the joined block text with the same metrics
as ``tests/test_engine_accuracy.py``. Point it at SGLang or at
``scripts/serve_unlimited_ocr.py``. Run from the repository root:

    python scripts/check_unlimited_ocr.py http://127.0.0.1:30000/v1
"""

from __future__ import annotations

import argparse
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from omniocr.application.metrics import character_error_rate, word_error_rate
from omniocr.domain.models import TenantContext
from omniocr.domain.result import Ok
from omniocr.infrastructure.unlimited_ocr import UnlimitedOCREngine

CORPUS = Path("tests/corpus")
FIXTURES = ("polytonic-scan-1", "polytonic-scan-2", "polytonic-scan-3")


@dataclass(frozen=True)
class _Page:
    number: int
    content: bytes
    width: int
    height: int


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(text.split()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("url", help="OpenAI-compatible base URL, e.g. http://host:30000/v1")
    parser.add_argument("fixtures", nargs="*", default=list(FIXTURES))
    args = parser.parse_args()

    engine = UnlimitedOCREngine(api_url=args.url)
    context = TenantContext("cli", "cli", "desktop")
    for fixture in args.fixtures:
        path = CORPUS / f"{fixture}.png"
        with Image.open(path) as image:
            width, height = image.size
        start = time.time()
        result = engine.extract(_Page(1, path.read_bytes(), width, height), context)
        seconds = time.time() - start
        if not isinstance(result, Ok):
            print(f"{fixture} failed: {result}", flush=True)
            continue
        hypothesis = _normalize(" ".join(block.text for block in result.value))
        reference = _normalize((CORPUS / f"{fixture}.txt").read_text(encoding="utf-8"))
        print(
            f"{fixture} {width}x{height} blocks={len(result.value)} "
            f"cer={character_error_rate(reference, hypothesis):.4f} "
            f"wer={word_error_rate(reference, hypothesis):.4f} secs={seconds:.0f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
