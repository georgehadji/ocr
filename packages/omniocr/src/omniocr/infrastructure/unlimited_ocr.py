"""Unlimited-OCR-2 (``baidu/Unlimited-OCR-2``) as an opt-in VLM engine.

A dedicated document-OCR model rather than a general chat VLM. Measured on the
three scan-tier fixtures it read polytonic Greek at mean CER ~0.068 against
the corrected references, with no hallucinated text — but it also confuses
diacritics in ways that change meaning (``ἡ`` read as ``ἢ``). It is therefore
wired exactly like ``VLMEngine``: one more voter, never the unaudited source
(CLAUDE.md rule 1). Its blocks carry zero confidence, so it loses every
confidence-weighted choice and mostly reaches the output through the aligned
merge suggestion. Like ``VLMEngine`` it can still be *selected* by
``ScriptAwareReconciler``'s homoglyph rule, on a line where it alone stayed in
Greek script.

The model is served behind an OpenAI-compatible endpoint (SGLang, or any
server that speaks ``/chat/completions``); ``api_url`` points at it. Nothing
here imports torch, so the adapter costs nothing when unused.

Output format, one page::

    <PAGE><BLOCK>text[126, 140, 819, 221]<FORK>first line of the paragraph
    second line
    <BLOCK>footer[450, 908, 523, 917]<FORK>— 21 —

Boxes are per *block* in 0–1000 coordinates of the image; content keeps the
printed line breaks and hyphenation. The pipeline assigns each block to the
single layout line it overlaps most, so a whole paragraph emitted as one block
would land on one line. Each block is therefore split into one block per text
line, stacked in equal-height strips of the block box.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from omniocr.domain.models import BBox, Confidence, OCRBlock
from omniocr.infrastructure.vlm import VLMEngine

DEFAULT_MODEL = "baidu/Unlimited-OCR-2"
# The model's own default instruction for page parsing (``model.infer``).
DEFAULT_PROMPT = "Multi page parsing."
# The parent's 1400 px cap exists because cloud VLMs bill per tile; a
# self-hosted model does not, and the spike's CER was measured on full-size
# pages. 3508 px is the long edge of an A4 page at ingest's 300 DPI, so pages
# reach the model as ingest rendered them.
DEFAULT_MAX_EDGE_PX = 3508
# Block labels that never carry recognizable text.
_SKIPPED_LABELS = frozenset({"image"})
_COORD_SPACE = 1000

_COORD = r"\s*(-?\d+(?:\.\d+)?)\s*"
_BLOCK = re.compile(
    r"<BLOCK>\s*(?P<label>[^\[\]<>]+?)\s*"
    rf"\[{_COORD},{_COORD},{_COORD},{_COORD}\]"
    r"\s*<FORK>(?P<content>.*?)(?=<BLOCK>|<PAGE>|\Z)",
    re.DOTALL,
)
# Only the model's own tokens and the HTML it emits for tables. Not every
# ``<…>``: critical editions print editorial supplements as ``<καὶ>``, and
# stripping those would silently delete recognized text (CLAUDE.md rule 1).
_MARKUP = re.compile(
    r"<\|[^|>]*\|>"
    r"|</?(?:PAGE|BLOCK|FORK|table|thead|tbody|tfoot|tr|td|th|caption|br)\b[^>]*>",
    re.IGNORECASE,
)


def _coords(match: re.Match[str]) -> tuple[int, int, int, int]:
    """Block box as ``(left, top, right, bottom)`` thousandths, clamped and ordered."""
    x1, y1, x2, y2 = (
        min(_COORD_SPACE, max(0, round(float(match.group(index))))) for index in range(2, 6)
    )
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


class UnlimitedOCREngine(VLMEngine):
    """Unlimited-OCR-2 over an OpenAI-compatible endpoint, split to line blocks."""

    name = "unlimited_ocr"
    # A dense polytonic page is ~2,500 output tokens; a local GPU run took
    # ~160 s per page on an 8 GB laptop card.
    max_tokens = 8192
    timeout_s = 600

    def __init__(
        self,
        api_url: str,
        model: str = DEFAULT_MODEL,
        api_key: str = "EMPTY",
        prompt: str = DEFAULT_PROMPT,
        max_edge_px: int = DEFAULT_MAX_EDGE_PX,
    ) -> None:
        # Self-hosted servers usually ignore the key; "EMPTY" is the
        # OpenAI-client convention for "none configured".
        super().__init__(
            api_key=api_key,
            api_url=api_url,
            model=model,
            site_name="",
            prompt=prompt,
            max_edge_px=max_edge_px,
        )

    def _parse_response(
        self, data: Any, page_width: int, page_height: int, scale: float = 1.0
    ) -> list[OCRBlock]:
        """Parse ``<BLOCK>`` output into one block per printed line.

        ``scale`` is ignored: boxes are normalized to the image, so they map
        onto the full-resolution page regardless of how much it was shrunk.

        Raises ``ValueError`` (which ``extract`` turns into an ``Err``) when
        the response is malformed or has text but no ``<BLOCK>`` structure — a
        wrong prompt or a chat-style reply must show up as an engine failure,
        not as a blank page. Unlike the parent there is no unstructured
        fallback: a full-page box on every word would assign them all to
        whichever line is largest.
        """
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"malformed Unlimited-OCR response: {exc!r}") from exc
        if not isinstance(text, str):
            raise ValueError(f"Unlimited-OCR content is {type(text).__name__}, not text")

        matches = list(_BLOCK.finditer(text))
        if not matches:
            if _MARKUP.sub("", text).strip():
                raise ValueError("Unlimited-OCR output has no <BLOCK> structure")
            return []
        if choice.get("finish_reason") == "length":
            # Cut off at max_tokens: the last block's text is incomplete and
            # its line count too short for the strips to land on real lines.
            matches = matches[:-1]

        run = self._engine_run()
        blocks: list[OCRBlock] = []
        for block_index, match in enumerate(matches):
            if match.group("label").strip().lower() in _SKIPPED_LABELS:
                continue
            lines = [
                line.strip()
                for line in _MARKUP.sub(" ", match.group("content")).split("\n")
                if line.strip()
            ]
            if not lines:
                continue
            x1, y1, x2, y2 = _coords(match)
            left = x1 * page_width // _COORD_SPACE
            top = y1 * page_height // _COORD_SPACE
            width = max(1, x2 * page_width // _COORD_SPACE - left)
            # shortcut: equal strips assume even leading inside a block; switch
            # to aligning against engine line boxes if blocks mix type sizes.
            strip = max(1, y2 * page_height // _COORD_SPACE - top) / len(lines)
            for line_index, line in enumerate(lines):
                strip_top = top + round(line_index * strip)
                strip_bottom = top + round((line_index + 1) * strip)
                blocks.append(
                    OCRBlock(
                        id=f"{self.name}-{block_index}-{line_index}",
                        text=unicodedata.normalize("NFC", " ".join(line.split())),
                        confidence=Confidence(0.0),
                        bbox=BBox(x=left, y=strip_top, w=width, h=max(1, strip_bottom - strip_top)),
                        provenance=run,
                    )
                )
        return blocks


__all__ = ["DEFAULT_MAX_EDGE_PX", "DEFAULT_MODEL", "DEFAULT_PROMPT", "UnlimitedOCREngine"]
