# Polytonic reconstruction as a diacritic check

Proposal: monotonize OCR output, run it through an external polytonizer
([Polytone](https://github.com/) — local, `E:\Documents\Vibe-Coding\Polytone`),
and use the result to catch diacritic errors. OCR recognizes *letters* far more
reliably than *marks*, so a second opinion on marks alone is worth having.

**Verdict: viable as a suggest-only check, gated by `Script`. Not viable as a
transformation, and not viable at all on dative-heavy material.** Measurements
below.

## 1. Why it can never rewrite text

Monotonic→polytonic is one-to-many. Breathings collapse entirely, and the
dictionary cannot break the tie because *both* candidates are real words:

| Polytonic | Monotonic | Both in lexicon? |
|---|---|---|
| ὄρος (mountain) / ὅρος (boundary) | όρος | yes |
| ἐξ (out of) / ἕξ (six) | εξ | yes |
| εἰς (into) / εἷς (one) | εις | yes |
| αὐτοῦ (of him) / αὑτοῦ (of himself) | αυτού | yes |

A wrong guess here silently rewrites recognized text and is undetectable
downstream, because the output is still well-formed Greek. CLAUDE.md rule 1
forbids it. Polytone flags `όρος` as `ambiguous` itself — the tool agrees.

## 2. Measured round-trip fidelity

Real polytonic text → mechanical monotonization → Polytone → compare against
the original. Every mismatch is a would-be false suggestion.

**54 words, 6 sentences: 88.9% exact.**

All three dative-free sentences round-tripped **perfectly**. All three failures
contained datives:

| Original | Returned | Class |
|---|---|---|
| ἀρχῇ | ἀρχὴ | dative → nominative |
| τῇ | τὴ | dative → nominative |
| ἡμέρᾳ | ἡμέρα | iota subscript lost |
| θεῷ | θεῶ | iota subscript lost |
| ἦν (×2) | ἢν | ancient imperfect — **self-reported `ambiguous`** |

Preserving the iota subscript through monotonization scores *worse* (85.2%):
Polytone leaves already-polytonic words untouched by design
(`λέξεις με πολυτονικά σημάδια μένουν άθικτες`), so the subscript suppresses
its own breathing-mark pass and `ἀρχῇ` degrades to `αρχῂ`.

The dative is a **grammatical** distinction, not a lexical one. Polytone
declares the limit itself: `Χωρίς POS tagger`. No monotonization tweak
recovers it.

### The dangerous part

The iota-subscript losses appear in **none** of `unknown`, `ambiguous`, or
`guessed`. Polytone is confident and wrong. A naive "confident + disagrees →
suggest" gate would fire on every dative in an Ancient or Byzantine text —
precisely the false-suggestion flood already fixed twice in the lexicon path.

## 3. Design that follows from the measurements

1. **Suggest-only.** Never touch `OCRLine.text`. Emit `Suggestion` and let the
   review UI decide, exactly as the VLM is reconciled rather than trusted.
2. **Gate by `Script`.** Enable for `MODERN` and `POLYTONIC` (modern Greek in
   polytonic orthography has no dative). Disable for `ANCIENT` and
   `BYZANTINE` until a POS tagger exists. `PONTIAN` is suggest-only by rule 3
   anyway.
3. **Compare blind to iota subscript.** Strip it from *both* sides before
   diffing. Polytone has no opinion worth having there, so a difference is not
   evidence. This is the same "normalize what you compare, never what you
   store" move as the lexicon fix.
4. **Suppress `unknown` / `guessed`.** Where Polytone has no lexicon entry, the
   box-grounded OCR reading wins outright. Only emit when Polytone is
   confident, or when it says `ambiguous` *and* disagrees — that case is a
   genuine minimal pair and is exactly what a human reviewer should see.
5. **Monotonization must keep the tonos.** Stripping every mark yields invalid
   monotonic Greek and Polytone degrades to smooth-breathing guesses
   (`ἀνθρωπος`, `ὀτι`, `ἠρθε`). Strip breathings and iota subscript, map
   circumflex and grave to acute, keep acute and diaeresis.

Note this differs from `ports/lexicon._monotonize`, which strips the accent
too. That one is a *comparison key* where both sides are stripped equally;
this one must produce text a Greek reader would accept. Two jobs, two
functions.

## 4. Integration constraints

- **~8 seconds per invocation** (37 MB `lexicon.json` parsed on every start).
  Per-line is impossible (3000 lines ≈ 6.7 h). One subprocess call per
  **document**; results split on newline, and a line-count drift must reject
  the batch wholesale rather than risk misalignment.
- This rules out `IPostCorrector`, which is per-line
  ([pipeline.py:539](../packages/omniocr/src/omniocr/application/pipeline.py#L539)).
  The check belongs at document level, after assembly and before export.
- **Polytone is GPL-3.0** (its lexicon derives from `el-polyton`); OmniOCR is
  MIT. Subprocess isolation is therefore a **licensing requirement**, not a
  convenience — never an import, never bundled. Same treatment Calamari
  already gets.
- Deterministic (pure lexicon lookup plus rules), so results are cacheable and
  tests are stable.

## 5. Honest status

The 88.9% figure is six hand-written sentences, not a corpus. Before shipping
this beyond `MODERN`/`POLYTONIC`, measure it against `tests/corpus/` with real
diplomatic ground truth (F-2) and report per-`Script` accuracy. The gate in §3.2
is set from a clean but small signal — it should be re-derived from that
measurement, not trusted from this document.
