"""Optional VLM engine adapter behind an opt-in toggle with grounding guard.

Per ARCHITECTURE.md §4 and BUILD_PLAN §4.8: the VLM is always opt-in,
always grounding-checked, and never the unaudited sole source.

The adapter defaults to `OpenRouter <https://openrouter.ai>`_ as the
API provider. Set ``api_key`` to your OpenRouter API key and optionally
configure ``site_url``/``site_name`` for OpenRouter ranking attribution.

To use a different OpenAI-compatible provider, pass its base URL as
``api_url`` (e.g. ``https://api.openai.com/v1``).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Sequence

from omniocr.domain.errors import EngineError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.infrastructure.grounding import GroundingGuard
from omniocr.ports.interfaces import IOCREngine, RawPage


class VLMEngine(IOCREngine):
    """Optional cloud-API vision-language model adapter.

    Defaults to `OpenRouter <https://openrouter.ai>`_ at
    ``https://openrouter.ai/api/v1`` with ``google/gemini-2.5-flash-001``
    as the default model. The adapter is API-compatible with any OpenAI-
    style chat completions endpoint.

    Requires an explicit ``api_key`` at construction time.
    The caller must check ``Settings.enable_vlm`` before wiring this adapter.

    Results are grounding-guarded: blocks whose bounding boxes do not overlap
    with a verifiable engine are returned as suggestions, never as source text.

    For OpenRouter thinking/reasoning models (e.g., DeepSeek R1), append
    ``:thinking`` to the model name:
    ``VLMEngine(api_key=..., model="deepseek/deepseek-r1:thinking")``.
    """

    name = "vlm"

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-2.5-flash-001",
        grounding_guard: GroundingGuard | None = None,
        site_url: str = "",
        site_name: str = "OmniOCR",
        prompt: str = (
            "Extract all visible text from this image in its original language. "
            "Return each line of text with its approximate bounding box "
            "in the format: x1,y1,x2,y2|text (one per line)."
        ),
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url.rstrip("/")
        self._model = model
        self._grounding_guard = grounding_guard or GroundingGuard()
        self._site_url = site_url
        self._site_name = site_name
        self._prompt = prompt

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            response = self._call_api(page.content)
            blocks = self._parse_response(response, page.width, page.height)
            return Ok(tuple(blocks))
        except Exception as exc:
            return Err(EngineError(f"VLM extraction failed: {exc}"))

    def extract_guarded(
        self,
        page: RawPage,
        context: TenantContext,
        engine_blocks: Sequence[OCRBlock],
    ) -> tuple[Sequence[OCRBlock], Sequence[OCRBlock]]:
        """Extract and grounding-guard VLM blocks against engine output.

        Returns (grounded_blocks, ungrounded_blocks) — the caller should
        only use grounded blocks as candidate text.
        """
        result = self.extract(page, context)
        if isinstance(result, Err):
            return (), ()
        vlm_blocks = list(result.value)
        grounded, _ = self._grounding_guard.filter(vlm_blocks, engine_blocks)
        return grounded, tuple(b for b in vlm_blocks if b not in grounded)

    def _call_api(self, image_bytes: bytes) -> Any:
        """POST the image to the VLM API (OpenRouter-compatible) and return the parsed response."""
        import base64
        import json
        import urllib.request

        encoded = base64.b64encode(image_bytes).decode("utf-8")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self._prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encoded}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 2048,
        }

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._site_name:
            headers["X-Title"] = self._site_name

        request = urllib.request.Request(
            f"{self._api_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_response(
        self, data: Any, page_width: int, page_height: int
    ) -> list[OCRBlock]:
        """Parse the API response into OCRBlocks."""
        timestamp = datetime.now(timezone.utc).isoformat()
        run = EngineRun(
            engine=self.name,
            model_ref=ModelRef(
                engine=self.name,
                model_name=self._model,
                model_hash="",
                params=(),
            ),
            model_hash="",
            params=(),
            timestamp=timestamp,
        )
        blocks: list[OCRBlock] = []

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return blocks

        for index, line in enumerate(text.strip().split("\n")):
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 1)
            coords = parts[0].strip()
            content = parts[1].strip()
            coord_parts = coords.split(",")
            if len(coord_parts) != 4:
                continue
            try:
                x, y, x2, y2 = int(coord_parts[0]), int(coord_parts[1]), int(coord_parts[2]), int(coord_parts[3])
            except (ValueError, TypeError):
                continue
            bbox = BBox(x=x, y=y, w=max(1, x2 - x), h=max(1, y2 - y))
            blocks.append(
                OCRBlock(
                    id=f"vlm-{index}",
                    text=content,
                    confidence=Confidence(0.0),
                    bbox=bbox,
                    provenance=run,
                )
            )

        if not blocks:
            # No parseable structure — attempt an unstructured fallback.
            for index, word in enumerate(text.strip().split()):
                if len(word) > 1:
                    blocks.append(
                        OCRBlock(
                            id=f"vlm-w{index}",
                            text=word.strip(".,;:!?"),
                            confidence=Confidence(0.0),
                            bbox=BBox(0, 0, page_width, page_height),
                            provenance=run,
                        )
                    )

        return blocks


__all__ = ["VLMEngine"]
