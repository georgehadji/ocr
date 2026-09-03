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

# Single source of truth for the defaults. Composition roots must reference
# these rather than restate them: desktop.py previously hardcoded its own
# ``https://api.openai.com/v1`` fallback while still passing an OpenRouter
# model id, so an unconfigured caller sent ``google/...`` to OpenAI.
DEFAULT_API_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"
# Longest-edge cap for the image sent to the VLM. Ingest renders at
# ``ingest.RENDER_DPI`` for box-grounded engines; the VLM bills per tile and
# does not need that resolution. See docs/VLM_COST_OPTIMIZATION.md.
DEFAULT_MAX_EDGE_PX = 1400


class VLMEngine(IOCREngine):
    """Optional cloud-API vision-language model adapter.

    Endpoint and model default to ``DEFAULT_API_URL`` / ``DEFAULT_MODEL``
    (OpenRouter, Gemini Flash-Lite) — cheaper than full Flash, vision-capable,
    adequate for deterministic extraction. The values live in those module
    constants and are deliberately not restated here.

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
        api_url: str = DEFAULT_API_URL,
        model: str = DEFAULT_MODEL,
        grounding_guard: GroundingGuard | None = None,
        site_url: str = "",
        site_name: str = "OmniOCR",
        prompt: str = (
            "Extract all visible text from this image in its original language. "
            "Return each line of text with its approximate bounding box "
            "in the format: x1,y1,x2,y2|text (one per line)."
        ),
        max_edge_px: int = DEFAULT_MAX_EDGE_PX,
    ) -> None:
        # api_url is operator/env-configured, not per-request input — but a
        # misconfigured value (file://, custom scheme) must fail at
        # construction, not silently open something other than an HTTP API.
        if not api_url.startswith(("http://", "https://")):
            raise ValueError(f"VLMEngine api_url must be http(s), got: {api_url!r}")
        if max_edge_px < 1:
            raise ValueError("max_edge_px must be positive")
        self._api_key = api_key
        self._api_url = api_url.rstrip("/")
        self._model = model
        self._grounding_guard = grounding_guard or GroundingGuard()
        self._site_url = site_url
        self._site_name = site_name
        self._prompt = prompt
        self._max_edge_px = max_edge_px

    def __repr__(self) -> str:
        masked = (
            self._api_key[:8] + "..." + self._api_key[-4:] if len(self._api_key) > 12 else "***"
        )
        return f"VLMEngine(model={self._model!r}, api_url={self._api_url!r}, api_key={masked!r})"

    def extract(
        self, page: RawPage, context: TenantContext
    ) -> Result[Sequence[OCRBlock], EngineError]:
        try:
            payload_bytes, scale = self._downscale(page.content)
            response = self._call_api(payload_bytes)
            blocks = self._parse_response(response, page.width, page.height, scale=scale)
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
        if not isinstance(result, Ok):
            return (), ()
        vlm_blocks = list(result.value)
        grounded, _ = self._grounding_guard.filter(vlm_blocks, engine_blocks)
        return grounded, tuple(b for b in vlm_blocks if b not in grounded)

    def _downscale(self, image_bytes: bytes) -> tuple[bytes, float]:
        """Shrink the page so the longest edge is at most ``max_edge_px``.

        Returns ``(image_bytes, scale)``. **The scale must be applied back to
        the model's returned coordinates**: the VLM reports boxes in the pixel
        space of the image it was handed, so a shrunken image yields shrunken
        boxes. Left uncorrected they would never overlap the full-resolution
        engine boxes, the grounding guard would discard every block, and the
        VLM would go silently dead while still being billed.

        Ingest renders at 300 DPI because Tesseract and Kraken need it for
        box-grounded recognition. The VLM inherited that resolution by accident
        — it consumes the same RawPage — and pays for it: vision models bill
        per tile, so at 300 DPI an A5 page is ~12 tiles (~3,100 tokens) and A4
        ~20 (~5,160). Tile count is a ceiling on *both* axes, so halving the
        dimensions roughly quarters the cost.

        Downscaling is safe here in a way it would not be in a system that
        trusted the VLM: its output is only used when the grounding guard
        matches it against box-grounded engine output, so an illegible image
        yields ungrounded blocks that get discarded. The failure mode is wasted
        spend, not corrupted text — which also means the grounded/ungrounded
        ratio is the signal to tune this against.

        Returns the original bytes unchanged if the image is already small
        enough, or if Pillow is unavailable (the VLM extra does not depend on
        it, and paying full price beats failing the page).
        """
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes))
            longest = max(image.width, image.height)
            if longest <= self._max_edge_px:
                return image_bytes, 1.0
            scale = self._max_edge_px / longest
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            buffer = io.BytesIO()
            resized.save(buffer, format="PNG")
            return buffer.getvalue(), scale
        except Exception:
            return image_bytes, 1.0

    def _call_api(self, image_bytes: bytes) -> Any:
        """POST the image to the VLM API (OpenRouter-compatible) and return the parsed response.

        Expects bytes already sized by ``_downscale`` — the caller owns that so
        it can keep the scale factor needed to map coordinates back.
        """
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
                        # No `detail` hint: it is an OpenAI convention that
                        # OpenRouter does not document for other providers, so
                        # on the default Gemini model it is either ignored (and
                        # misleading) or honoured as ~85 tokens (a thumbnail
                        # that cannot resolve polytonic diacritics). Resolution
                        # is controlled by actually resizing instead — see
                        # _downscale.
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.0,
            "top_p": 1.0,
        }
        if "gemini" in self._model.lower():
            payload["reasoning"] = {"effort": "minimal"}
        elif "claude" in self._model.lower():
            payload["reasoning"] = {"effort": "low"}

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
        with urllib.request.urlopen(  # nosec B310 - scheme validated in __init__
            request, timeout=120
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_response(
        self, data: Any, page_width: int, page_height: int, scale: float = 1.0
    ) -> list[OCRBlock]:
        """Parse the API response into OCRBlocks.

        ``scale`` is the factor ``_downscale`` applied to the image before it
        was sent. Coordinates come back in the sent image's pixel space, so
        they are divided by it to land back in full-page space where the
        grounding guard can compare them against engine boxes.
        """
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
                x, y, x2, y2 = (
                    int(coord_parts[0]),
                    int(coord_parts[1]),
                    int(coord_parts[2]),
                    int(coord_parts[3]),
                )
            except (ValueError, TypeError):
                continue
            if scale != 1.0:
                x, y, x2, y2 = (
                    round(x / scale),
                    round(y / scale),
                    round(x2 / scale),
                    round(y2 / scale),
                )
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
