"""Serve Unlimited-OCR-2 behind a minimal OpenAI-compatible ``/chat/completions``.

A stand-in for SGLang (Linux-only) so ``UnlimitedOCREngine`` can be exercised
over HTTP on any machine with a CUDA GPU. Only what the adapter sends is
handled: the request's text part becomes ``model.infer``'s prompt and its
data-URL image becomes the page. It is a measurement tool, not a server to
deploy: one request at a time, no auth, bound to localhost.

Needs its own environment (torch, transformers and the model's remote code
pins clash with Kraken's), and the model downloaded locally:

    python -I scripts/serve_unlimited_ocr.py /path/to/Unlimited-OCR-2 --port 30000

``--ngram-guard`` applies the model card's ``no_repeat_ngram_size=35,
ngram_window=128``. The adapter does not send those, so leave it off to
measure what a default server sees.

``finish_reason`` is ``"length"`` when generation used all of ``max_tokens``,
else ``"stop"`` — derived here, not reported by a real server.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import torch
from PIL import Image
from transformers import AutoModel, AutoTokenizer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("model", help="local Unlimited-OCR-2 model directory")
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--ngram-guard", action="store_true")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = (
        AutoModel.from_pretrained(
            args.model, trust_remote_code=True, use_safetensors=True, torch_dtype=torch.bfloat16
        )
        .eval()
        .cuda()
    )
    ngram: dict[str, int] = (
        {"no_repeat_ngram_size": 35, "ngram_window": 128} if args.ngram_guard else {}
    )
    print(f"loaded, ngram guard {'on' if ngram else 'off'}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body: dict[str, Any] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            parts: list[dict[str, Any]] = body["messages"][0]["content"]
            prompt = next(str(p["text"]) for p in parts if p["type"] == "text")
            url = next(str(p["image_url"]["url"]) for p in parts if p["type"] == "image_url")
            image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
            max_tokens = int(body["max_tokens"])
            print(
                f"request prompt={prompt!r} image={image.size} max_tokens={max_tokens}", flush=True
            )
            start = time.time()
            result = model.infer(
                tokenizer,
                prompt=prompt,
                image_file=image,
                eval_mode=True,
                max_new_tokens=max_tokens,
                return_dict=True,
                **ngram,
            )
            n_tokens = len(result["token_ids"])
            finish = "length" if n_tokens >= max_tokens else "stop"
            print(f"done {time.time() - start:.1f}s tokens={n_tokens} finish={finish}", flush=True)
            out = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": result["text"]},
                            "finish_reason": finish,
                        }
                    ]
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
