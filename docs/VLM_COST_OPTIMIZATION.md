# VLM Token Consumption — Research & Optimization Plan

**Date:** 2026-08-06
**Scope:** `packages/omniocr/src/omniocr/infrastructure/vlm.py` — the opt-in VLM
second-opinion engine. Not this repo's Claude Code usage.
**Status:** Research. No code changed yet.

---

## TL;DR

The headline recommendation you'd expect — *"turn on prompt caching"* — **does
not apply to this workload**, and the reason matters (§2). The actual cost is
almost entirely **image tokens**, and there are three real levers:

| # | Lever | Est. saving | Effort |
|---|---|---|---|
| 1 | Wire the `CachingEngine` that already exists but is used nowhere | 100% on re-runs | ~1 line |
| 2 | Downscale the image before sending (300 DPI → ~150 DPI) | **~65–75%** | small |
| 3 | Make the ingest DPI independent of the VLM send DPI | enables #2 safely | small |

---

## 1. What a book actually costs today

`infrastructure/ingest.py` renders every PDF page at **300 DPI**:

```python
matrix = fitz.Matrix(300 / 72, 300 / 72)
```

Gemini tokenizes images in **768×768 tiles at 258 tokens per tile**; images
≤384px in both dimensions are a flat 258 tokens ([Gemini token
docs](https://ai.google.dev/gemini-api/docs/tokens)).

At 300 DPI:

| Page size | Pixels @300dpi | Tiles (⌈w/768⌉ × ⌈h/768⌉) | Image tokens/page |
|---|---|---|---|
| A5 (148×210mm) | 1749 × 2480 | 3 × 4 = 12 | **~3,096** |
| A4 (210×297mm) | 2481 × 3507 | 4 × 5 = 20 | **~5,160** |

A 400-page A5 book ≈ **1.24M input tokens**. A4 ≈ **2.06M**. Per full run,
per book. The text prompt is ~40 tokens — **rounding error** by comparison.

`_process_page` caches engine results per page (`engine_results` keyed by
`id(engine)`), so the VLM is called **once per page**, not once per line. That
part is already correct and worth preserving.

---

## 2. Why prompt caching does *not* help here

Worth stating explicitly, because it's the intuitive first answer and it's wrong
for this shape of workload:

- **Below the minimum.** Every provider has a floor before caching engages:
  OpenAI 1,024 tokens, Gemini 2.5 Flash 1,024, Gemini 2.5 Pro 4,096, Anthropic
  1,024–4,096 ([OpenRouter caching
  docs](https://openrouter.ai/docs/guides/best-practices/prompt-caching)). This
  code's prompt is ~40 tokens. It will never reach a cache threshold.
- **Nothing repeats.** Caching pays off on a large *shared prefix*. Here the
  only repeated content is that tiny prompt; the expensive part — the image —
  is unique per page by definition.
- **TTL is shorter than the work.** Gemini's cache lives "on average 3–5
  minutes"; Anthropic's default is 5 minutes. At the measured ~500s/page for the
  CPU ensemble, a cache entry expires before the next page needs it.

**Conclusion:** prompt caching is the wrong tool here. Skip it. (It *would*
matter if the prompt grew large — e.g. few-shot examples or a glossary — which
is a reason to be wary of growing it.)

---

## 3. Finding: `CachingEngine` exists and is wired nowhere

`infrastructure/resilience.py` already implements `CachingEngine` — LRU,
optional TTL, keyed on `sha256(page.content)` + org + custom model id. It is
exported, contract-tested, and unit-tested.

`grep -rn CachingEngine` across the repo: it appears in `resilience.py`, the
`__init__` re-export, and two test files. **It is referenced by zero composition
roots.** `create_ensemble_pipeline` wraps engines in `RetryingEngine` only.

Consequences today:
- Re-running the same document re-bills every page.
- Resuming a crashed job re-bills every page already processed *in that run*
  (the job checkpoint restores pages, but nothing prevents re-calling the VLM
  for pages that get reprocessed).
- Two documents sharing a page (a reprint, a duplicated scan) pay twice.

**Fix:** wrap the VLM in `CachingEngine` inside `create_ensemble_pipeline`.
This is roughly a one-line change and needs no new code.

**Caveat — the current cache is in-memory only.** `OrderedDict`, `max_size=128`.
For a 400-page book at default size it evicts continuously, and it does not
survive a process restart. If it's worth caching VLM calls at all (it is —
they're the expensive ones), it's worth a persistent variant keyed the same
way. A SQLite-backed cache reusing the existing `sha256(page.content)` key
would make resume genuinely free.

---

## 4. Finding: `"detail": "low"` is probably a no-op — and either way it's a bug

`_call_api` sends:

```python
"image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "low"}
```

`detail` is an **OpenAI convention**. OpenRouter's multimodal documentation does
not document `detail` support or normalization for non-OpenAI providers, and
Gemini's own resolution control is a different parameter (`media_resolution` on
Gemini 3.x). The default model here is `google/gemini-3.5-flash-lite`.

So one of two things is true, and both are problems:

1. **It's ignored (most likely).** You are paying full 258-tokens-per-tile
   price, and the code contains a comment-free parameter implying a cost control
   that isn't happening.
2. **It's honored.** OpenAI's `detail: "low"` caps an image at **85 tokens** —
   a ~512px thumbnail. That is far below what's needed to read printed Greek
   diacritics. OCR quality would be silently destroyed.

Either way this needs resolving, and the honest fix is to control resolution
**explicitly on our side** (§5) rather than relying on a provider-specific flag
that may or may not be honored.

**Action:** measure it. One API call with `usage` inspected tells you which
branch you're in — the response reports actual prompt tokens.

---

## 5. The real lever: downscale before sending

300 DPI exists for a good reason — Tesseract and Kraken need it for accurate
box-grounded recognition. **But the VLM doesn't need the same resolution**, and
right now it inherits it by accident because both consume the same `RawPage`.

Because tiles are a **ceiling function on both axes**, halving the dimensions
roughly quarters the tile count:

| Send DPI | A5 pixels | Tiles | Tokens/page | vs 300 DPI |
|---|---|---|---|---|
| 300 | 1749 × 2480 | 3 × 4 = 12 | 3,096 | — |
| 200 | 1166 × 1654 | 2 × 3 = 6 | 1,548 | **−50%** |
| 150 | 875 × 1240 | 2 × 2 = 4 | 1,032 | **−67%** |
| 100 | 583 × 827 | 1 × 2 = 2 | 516 | **−83%** |

A 400-page A5 book at 150 DPI ≈ **413k tokens instead of 1.24M**.

**The constraint this must respect:** the VLM's output is only ever used when
the grounding guard matches it against box-grounded engine output
(`extract_guarded`). So VLM legibility degrading gracefully is *safer* here than
in a system that trusted the VLM directly — an unreadable VLM produces
ungrounded blocks, which get discarded. The failure mode is wasted spend, not
corrupted text.

**Therefore:** downscaling is a tuning knob with a measurable quality signal
already built in — *the grounded/ungrounded ratio*. Sweep the DPI down until the
grounded ratio starts dropping, then back off one step. That's an empirical
calibration, not a guess, and `extract_guarded` already returns both halves of
the number needed to measure it.

---

## 6. Smaller items

- **`max_tokens: 2048` on output.** Reasonable for a page of text; output tokens
  are usually priced higher than input, so don't raise it casually. Worth
  measuring actual completion lengths — if pages come back at ~600 tokens, this
  ceiling is fine as a safety bound.
- **PNG vs JPEG.** The payload is base64 PNG. Tile count depends on *pixel
  dimensions*, not file size, so JPEG **does not reduce token cost** — only
  upload bandwidth and latency. Not a token lever; don't bother for cost.
- **Don't grow the prompt.** It's ~40 tokens now. Few-shot examples or a
  Greek glossary would add per-page cost on every page and still likely sit
  under the caching threshold — worst of both.
- **`reasoning: {"effort": "minimal"}`** is already set for Gemini. Good — this
  is the right instinct; reasoning tokens on a transcription task are waste.

---

## 7. Recommended order

1. **Measure first.** One instrumented call logging `usage.prompt_tokens`
   settles §4 and gives a real per-page baseline. Everything else is sized off
   that number.
2. **Wire `CachingEngine` into `create_ensemble_pipeline`.** ~1 line, no new
   code, eliminates all re-run cost.
3. **Add an explicit VLM send-DPI**, defaulting to ~150, decoupled from ingest
   DPI. Sweep against the grounded/ungrounded ratio to calibrate.
4. **Then** consider a persistent (SQLite) cache if resume-cost matters in
   practice — gate this on whether real runs actually resume often.

Steps 1–3 are small and independent. Step 4 is only worth it if measurement
says so.

---

## Sources

- [OpenRouter — Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching)
- [OpenRouter — Prompt caching / cached token costs](https://openrouter.ai/blog/tutorials/prompt-caching-sticky-routing/)
- [OpenRouter — Multimodal / images](https://openrouter.ai/docs/features/multimodal/images)
- [Gemini API — Understand and count tokens](https://ai.google.dev/gemini-api/docs/tokens)
- [Gemini API — Image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)
