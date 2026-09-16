# OmniOCR — Embedded PDF Text Layer (A11)

Plan of record for using a PDF's own embedded text instead of recognizing pixels,
**only where that text is demonstrably the whole page**.

Status: specified and measured against the target document before any code was written.
Numbers in this document come from `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf`
(74 pages), measured 2026-09-16 with PyMuPDF 1.27.2.

---

## 1. The gap

`infrastructure/ingest.py::_stream_pdf` rasterises **every** page at `RENDER_DPI`
unconditionally. Grepping the package for `get_text`, `text_layer`, or `has_text` returns
nothing: there is no text-layer detection anywhere in the pipeline.

For a born-digital PDF that is a pure loss. The page already carries exact characters with
exact boxes; OmniOCR throws them away, renders the page to a bitmap, and recognizes it at a
measured **CER 0.1226**. The correct answer was in the file.

## 2. Why the obvious fix is a trap

The naive rule — *"if the page has a text layer, use it"* — would be catastrophic on
exactly the material this project targets.

`microsoft/markitdown`, which was the prompt for this work, implements precisely that rule:
its `_pdf_converter.py` runs pdfplumber with a pdfminer fallback, has **no coverage check
and no rasterisation fallback**, and on empty extraction "simply returns empty markdown".

Pointed at page index 20 of the target book it would return this, and call it success:

```
— 21 —
ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ ΤΗΣ ΘΕΣΣΑΛΟΝΙΚΗΣ
```

44 characters against a 2,973-character human transcription. **CER 0.9939.**

The book is a scan whose producer stamped a running head and folio onto each page as real
text. The body is pixels. A presence test cannot tell that apart from a born-digital page,
and getting it wrong costs eight times more accuracy than the feature could ever win.

## 3. What the target document actually looks like

Measured per page over all 74 pages. `img_cov` is the largest embedded image's area as a
fraction of page area; `txt_cov` is the summed area of text line boxes over page area.

| Page class | Count | `img_cov` | `txt_cov` | chars |
|---|---|---|---|---|
| Scanned body (full-page image + stamped running head) | 62 | ≈ 1.95 | **0.010 max** | 44 |
| Born-digital front matter | 5 | 0.000 | **0.433 – 0.552** | 2,065 – 2,540 |
| Blank / sparse front matter | 7 | 0.000 | 0.000 – 0.063 | 0 – 106 |

Two findings decide the design:

1. **The book is mixed.** Its front matter is genuinely born-digital and its body is
   genuinely a scan. Any gate must therefore be **per page**, not per document — and this
   one document exercises both branches, so it is its own integration fixture.
2. **The separation is enormous.** Worst born-digital body page (0.433) sits **42× above**
   the worst scan page (0.010). A single threshold is not a guess here; it is a line drawn
   across a two-order-of-magnitude gap.

## 4. The gate

A page's embedded text is used **only** when both hold:

| # | Check | Rationale |
|---|---|---|
| G1 | The page embeds **no raster image at all** | A page that carries an image is either a scan or a figure. Both are cases where the text layer is, at best, partial. This is also the only defence against the **scanner-OCR** class — PDFs where some other tool has already stamped invisible OCR text over a full-page scan. Those pass any coverage test with flying colours while carrying exactly the low-quality recognition OmniOCR exists to beat. |
| G2 | `txt_cov ≥ TEXT_COVERAGE_MIN` (**0.10**) | Rejects the running-head trap. Ten times the worst observed scan page, four times below the worst real body page. |

Both checks fail **safe**: a rejected page is rasterised and recognized exactly as today.
The feature can forgo a speedup; it can never degrade output.

`TEXT_COVERAGE_MIN = 0.10` costs one real page in this book — index 2, a sparse title page
at `txt_cov` 0.063 with 106 characters. It gets OCR'd. That is the correct trade: 106
characters of possible gain against the risk of admitting a page whose layer is a caption.

### Order matters, for cost

G1 runs first, and on a scanned book it is the only check that ever runs. Measured cost per
page on this document:

| Call | Scan page | Born-digital page |
|---|---|---|
| `page.get_images(full=True)` | **9.9 ms** | — |
| `page.get_text("words")` | 187.7 ms | 33.6 ms |
| `page.get_text("dict")` | **4,904.8 ms** | 53.3 ms |
| `page.get_image_rects(...)` | **1,694.6 ms** | — |

`get_text("dict")` on a scan page costs **4.9 seconds** — MuPDF decodes the embedded image
to report it as a block. `get_image_rects` costs 1.7 s for the same reason. **Neither is
used.** The implementation uses `get_images` to gate and `get_text("words")` to extract,
which is also the only mode that returns word boxes with `(block_no, line_no, word_no)` —
everything needed, in one call.

Total added cost for the 74-page target book: **62 × 9.9 ms ≈ 0.6 s**, against an OCR run
measured in tens of minutes.

## 5. Architecture

Dependencies point inward: `domain → ports → application → infrastructure → composition →
interfaces`. The change respects that and adds no new dependency — PyMuPDF is already the
ingest backend.

### 5.1 New: `infrastructure/text_layer.py`

Owns everything that touches `fitz`, and returns **domain models**, the same contract
`tesseract.py` and `kraken.py` already satisfy.

```
extract_text_layer(page: fitz.Page, dpi: int, script: Script) -> tuple[OCRLine, ...]
```

* Returns `()` when the gate rejects the page. An empty result *is* the "recognize this
  page" signal — no second return value, no enum, no exception for a non-error.
* Words come from `get_text("words")`; lines are grouped by the `(block_no, line_no)` key
  the same call provides, so line breaking is the PDF producer's own, not a heuristic of
  ours.
* Coordinates are multiplied by `scale` (`RENDER_DPI / 72`) so every box lands in the same
  pixel space as the rasterised page. This is not cosmetic: the review UI overlays boxes on
  the rendered image and the searchable-PDF exporter positions its text layer from them.
* `script` is the document-level variety the composition root already configures its layout
  analyzer with, passed rather than detected. These lines skip layout, which is where the
  recognized path picks its script up; leaving them `UNKNOWN` would mean
  `SuggestOnlyCorrector` finds no lexicon for them and born-digital pages get a **quieter
  review** than recognized ones — the opposite of §5.3's intent. Found by checking, after
  the first version shipped `Script.UNKNOWN` and the pipeline test still passed because its
  fixture lexicon was keyed on `UNKNOWN` too.
* **Rotated pages are rejected** (`page.rotation != 0` → `()`). The target document is
  entirely unrotated, so a rotation transform here would ship untested and, if wrong,
  misplace every box on the page while the text looked perfect. A page that rotates gets
  OCR'd until there is a fixture that proves the transform.

### 5.2 Changed: `infrastructure/ingest.py`

* `ImagePage` gains `text_lines: tuple[OCRLine, ...] = ()`. Additive with a default, so
  `frozen=True, slots=True` is preserved and every existing construction site still works
  — the same pattern `OCRLine.agreement` used for A5.
* `DocumentPageSource.__init__` gains `text_layer: bool = True`.
* `_stream_pdf` calls the extractor while it already has the page open. Zero extra file
  I/O; the gate is 9.9 ms on the pages that reject.
* The page is **still rasterised** even when the text layer wins. The image is what the
  human reviews against (CLAUDE.md rule 1), and rendering is not the dominant cost —
  recognition is. Skipping it would trade an unmeasured saving for a broken review UI.

`RawPage` in `ports/interfaces.py` is deliberately **not** extended. It is a structural
Protocol with four implementations; adding a required property would break all of them for
a field only the PDF source can populate. The pipeline reads the attribute defensively,
which is the house pattern already used for `provenance.variant`, `raw_page.width`, and
`lexicon.layers`.

### 5.3 Changed: `application/pipeline.py`

One early branch at the top of `_process_page`:

```python
text_lines = getattr(raw_page, "text_lines", ())
if text_lines:
    return self._page_from_text_layer(raw_page, text_lines, context)
```

This must read `raw_page`, **before** `_variant_pages` — preprocessing returns new page
objects that do not carry the field, by design.

`_page_from_text_layer` runs post-correction and nothing else:

* **No preprocessing, no layout segmentation, no engines.** The PDF already states its own
  line structure; segmenting a rendered bitmap to rediscover it would be strictly worse.
* **No reconciliation.** There is one reading. Running OCR alongside it to vote would burn
  the entire time saving and then let self-reported confidence — the one signal this
  codebase has repeatedly caught lying — choose recognition over exact text.
* **Post-correction still runs.** A born-digital PDF can still contain a typo, and the
  lexicon and diacritic checks are suggest-only either way. Skipping them would silently
  give text-layer pages a weaker review than recognized ones.

### 5.4 Faithfulness and provenance

Rule 1 is *no stage may silently alter recognized text*. Nothing here alters text; the
concern is the weaker one of **attribution** — an export must never imply that OmniOCR
recognized something it copied.

* Every line carries `EngineRun(engine="pdf_text_layer", ...)` with
  `ModelRef(engine="pdf_text_layer", model_name="embedded", model_hash="")`. ALTO/PAGE and
  the review UI already surface provenance, so this needs no exporter change.
* `agreement=AgreementTier.SINGLE` — one reading, stated honestly, and the review queue
  orders by it.
* `Confidence(100.0)` is **not** an estimate. It records that no recognition happened and
  there is therefore no recognition error to rank. These lines never enter a vote, so the
  number cannot outrank anything; it only says "do not queue this for review ahead of a
  contested line".

### 5.5 Changed: composition + CLI

`create_tesseract_pipeline` and `create_ensemble_pipeline` gain `text_layer: bool = True`,
forwarded to `DocumentPageSource`. CLI gains `--no-text-layer`.

The switch is not speculative. The CER gate must be able to force recognition on a page
that has a usable layer, or the gate goes blind on mixed documents in exactly the way it
was blind to preprocessing before A3 — a regression in recognition on a born-digital page
would otherwise be invisible because recognition never ran.

## 6. Tests — `tests/test_text_layer.py`

Every test builds its PDF in-process with `fitz`; none needs the copyrighted source.

**Gate**
1. A synthetic born-digital page (35 lines of Greek) is accepted.
2. A page carrying a full-page image is rejected, **even with dense text on top** — the
   scanner-OCR case, and the one G1 exists for.
3. A running-head-only page is rejected. This is the target book's trap, reproduced at
   44 characters.
4. A page just under `TEXT_COVERAGE_MIN` is rejected; just over is accepted.
5. A rotated page is rejected.
6. `text_layer=False` yields no lines anywhere.

**Extraction**
7. Word boxes scale by `RENDER_DPI / 72` and land inside the rasterised page bounds.
8. Boxes land on **ink**: crop each reported box out of the rendered pixmap and assert it
   is not blank. A scale error that keeps boxes in-bounds still fails this.
9. Line grouping follows the PDF's `(block_no, line_no)`, one `OCRLine` per source line,
   with `blocks` populated per word.
10. Text round-trips NFC-normalized (rule 5) and is otherwise byte-identical to the input.

**Pipeline**
11. A born-digital page produces lines with `provenance.model_ref.engine ==
    "pdf_text_layer"` and `agreement == SINGLE`, and **no engine is called** — asserted
    with a recording fake engine, because "it was fast" is not a test.
12. A scanned page routes to the normal path with the engines called as today.
13. Post-correction suggestions are still attached to text-layer pages.

**Regression**
14. `tests/test_ingest.py` unchanged and green: its one-page `insert_text` PDF has no
    image, so it now exercises the gate's coverage arm and must still stream a raster page.

## 7. What this does not do

* **No new dependency.** Not markitdown, not pdfplumber, not pdfminer. PyMuPDF already
  ships and already outperforms them here.
* **No multi-format ingest.** DOCX/EPUB/HTML is the one thing markitdown genuinely offers,
  and it is outside the scope locked in `ARCHITECTURE.md` §1 ("printed material").
* **No change to the corpus or to any accuracy baseline.** All three scan-tier fixtures are
  full-page images; the gate rejects them at G1 and recognition runs exactly as before.
  `docs/ENGINE_ACCURACY.md` numbers are untouched.

## 8. Definition of done

* Gate and extractor land with the tests in §6 passing.
* The target book's 74 pages split 5 text-layer / 69 recognized, confirmed by a run, not by
  reasoning.
* `mypy --strict` clean (rule 7); no `Any`, no `cast`.
* `docs/ARCHITECTURE.md` §3 stage 1 records that ingest gates on the text layer, so the
  pipeline diagram stops claiming an unconditional raster.
