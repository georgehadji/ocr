# LLM post-correction: catching errors like `μικροσκωπικοῦ`

Status: **design + evidence review, not implemented.** Written after measuring
Kraken and Tesseract against each other on the Δεδούσης scans (see
[ENGINE_ACCURACY.md](ENGINE_ACCURACY.md)).

## The error class we are trying to catch

On page 31 Kraken read `μικροσκωπικοῦ` where the page says `μικροσκοπικοῦ` —
one vowel, `ω` for `ο`. That error is representative and it is the hard case:

- it is a **single character**, so it survives any per-line confidence check
- the result is **phonotactically legal Greek**, so nothing structural flags it
  (in modern Greek `ω` and `ο` are homophones — precisely why the confusion
  exists in the typesetting and in the reader's ear)
- it is **not** a diacritic violation, so `_check_diacritics` passes it
- it **is** absent from any real Greek lexicon

That last property is the entire opening. The error is already detectable
without an LLM.

## What the existing pipeline already does

`SuggestOnlyCorrector` ([post_correction.py](../packages/omniocr/src/omniocr/application/post_correction.py))
runs per line and emits a `Suggestion` with reason `not_in_<lexicon>_lexicon`
for every token missing from the script's lexicon. `μικροσκωπικοῦ` triggers
that today.

So the gap is not detection. The gap is that the suggestion says only *"this
token is unknown"* and offers the token back unchanged as its own
`suggestion_text`. A human reviewer gets a flag, not a candidate. With a real
lexicon wired in (task F-8), a book this size will produce thousands of such
flags, most of them proper nouns and genuine rare words.

**The LLM's job is therefore ranking and proposing, not finding.** That
framing is what makes the idea safe and affordable.

## What the literature says, and why it argues for restraint

The 2025 results are consistent and directly relevant to this project:

- Gains from LLM post-correction are **strongest under moderate-to-high OCR
  noise; low-noise text is inherently sensitive to over-correction**
  ([HIPE-OCRepair / ICDAR 2026](https://arxiv.org/html/2607.08143)).

  **This premise weakened on 2026-09-03 and the conclusion has to be re-argued,
  not restated.** The 0.038 figure this section originally cited was one page.
  Measured across three scan-tier fixtures the same model scores a mean
  **0.126 CER** (`docs/ENGINE_ACCURACY.md`) — moderate noise, which is the
  regime where the literature reports post-correction gains are *strongest*,
  not weakest. The risk/reward argument below no longer follows from the
  numbers the way it did when written.

  What does not change is CLAUDE.md rule 1: post-correction suggests, the
  human decides. That is a faithfulness constraint, not a cost/benefit
  judgement, and it holds at any CER. So the restraint this document argues
  for survives — but on the grounds of faithfulness alone, with the empirical
  argument now pointing the other way. Anyone reopening this decision should
  start from 0.126, not 0.038.
- Post-correction requires restoring intended word forms **without introducing
  content absent from the source**; LLM fluency bias and hallucination are
  liabilities rather than assets here
  ([No Free Lunches, RESOURCEFUL 2025](https://aclanthology.org/2025.resourceful-1.8/)).
- A documented failure mode is **over-historicizing** — models inserting
  archaic forms that the source does not contain. The polytonic analogue is
  obvious and dangerous in both directions: a model may silently normalize
  polytonic to monotonic (its overwhelming training prior for Greek), or
  over-apply breathings and accents to text that lacks them.
- For Greek specifically, LLM correction shows promise **"especially for cases
  where recognition results are relatively poor"**
  ([Old Greek OCR Result Correction Using LLMs, DocEng 2025](https://dl.acm.org/doi/10.1145/3704268.3748676)).
  Again: the opposite of our regime.
- Language matters. The same study that improved historical English **failed to
  reach practically useful performance on Finnish** — a morphologically rich,
  lower-resource language. Polytonic Greek is at least as unfavourable.

Conclusion: an LLM layer here is defensible **only** as a suggestion ranker on
already-flagged tokens, never as a page rewriter. This is not a hedge; it is
what the measured evidence supports.

## Proposed design

A new adapter implementing the existing `IPostCorrector` port, composed
*after* `SuggestOnlyCorrector` rather than replacing it.

```
SuggestOnlyCorrector  ->  Suggestion(reason="not_in_greek_lexicon", ...)
                              |
                              v  only these lines, only these tokens
LlmSuggestionRanker   ->  Suggestion(reason="llm_candidate", confidence=...)
                              |
                              v
                        human review UI decides
```

### Hard constraints

Every one of these is a guardrail against a failure mode named above.

1. **Suggest-only.** Returns `Suggestion` objects. Never mutates `line.text`.
   CLAUDE.md rule 1; identical to every other corrector.
2. **Flagged tokens only.** Input is the set of tokens the lexicon already
   rejected — not the page, not the line. Bounds cost and blocks whole-page
   rewriting outright.
3. **Edit-distance cap.** Reject any candidate more than *k* edits from the
   recognized token (k=2 covers `ω`→`ο`, `ρ`→`ο`, missing breathing). A
   proposal beyond that is a hallucination, not a correction.
4. **Charset whitelist.** Candidates must contain only characters already
   present in the page's script range. Blocks the Latin/Cyrillic drift that
   the Russian over-historicizing study documents.
5. **Diacritic-density guard.** Reject candidates that change the count of
   breathings/accents by more than one, in either direction. This is the
   monotonic-normalization defence, and it is the one most specific to us.
6. **NFC on comparison only**, never as an orthographic change (rule 5).
7. **Abstention is a valid answer.** A model that returns "no candidate" must
   be able to; forcing a guess is how over-correction enters.

### Cost shape

Only flagged tokens travel, with a short window of surrounding context for
disambiguation. On the 435 lines measured so far, lexicon-flagged tokens are a
small fraction of the page. Batch per page, cache by token — proper nouns
recur heavily in a book about named monuments, so the cache hit rate should be
high.

### Where it plugs in

- Port: `IPostCorrector` — already exists, no interface change.
- Wiring: `composition/desktop.py`, opt-in behind an API key exactly as
  `VLMEngine` is today. Absent a key, the pipeline is unchanged.
- Same posture as the VLM: opt-in, and always reconciled against
  box-grounded output rather than trusted directly.

## How to know whether it actually helps

Not optional, and this is the part the literature is emphatic about — under
low noise these systems can measurably make text *worse*.

1. Transcribe ground truth for a page sample (the harness exists:
   [ground_truth.py](../packages/omniocr/src/omniocr/application/ground_truth.py),
   [evaluation.py](../packages/omniocr/src/omniocr/application/evaluation.py)).
2. Measure CER/WER before, and after *accepting every* suggestion.
3. Report **both** directions separately: errors fixed, and correct text
   broken. A net CER win that breaks correct polytonic forms is a loss for a
   diplomatic transcription.
4. Ship only if it clears a precision floor on the "was already correct" set.

Until that measurement exists, this stays a design document.

## Sources

- [OCR Error Post-Correction with LLMs in Historical Documents: No Free Lunches](https://aclanthology.org/2025.resourceful-1.8/)
- [ICDAR 2026 HIPE-OCRepair Competition on LLM-Assisted OCR Post-Correction](https://arxiv.org/html/2607.08143)
- [Evaluating LLMs for Historical Document OCR: A Methodological Framework](https://arxiv.org/pdf/2510.06743)
- [Old Greek OCR Result Correction Using LLMs (DocEng 2025)](https://dl.acm.org/doi/10.1145/3704268.3748676)
- [OCR for Greek polytonic (multi accent) historical printed documents](https://dl.acm.org/doi/10.1145/3322905.3322926)
- [Multimodal LLMs for OCR, OCR Post-Correction, and NER in Historical Documents](https://arxiv.org/pdf/2504.00414)
