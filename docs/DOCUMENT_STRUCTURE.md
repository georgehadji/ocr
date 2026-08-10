# Document structure recovery

Four defects observed in the Πολυχρονιάδης/Δεδούσης output — 850 unjoined
hyphenated line-ends (29% of lines), one paragraph per OCR line, running heads
and page numbers as body text, and a single `Normal` style — are **one missing
stage**, not four bugs.

## 1. Why they are one defect

`PipelineOrchestrator` builds `DocumentStructure(pages=...)`
([pipeline.py:228](../packages/omniocr/src/omniocr/application/pipeline.py#L228))
and hands it straight to the exporter
([pipeline.py:658](../packages/omniocr/src/omniocr/application/pipeline.py#L658)).
Nothing runs in between. `OCRParagraph` is defined in `domain/models.py:131`,
re-exported twice, and **constructed nowhere in the codebase**.

Everything the pipeline knows is *line-local*. Every one of the four defects
needs either the **neighbouring line** (hyphens, paragraphs) or the
**neighbouring pages** (running heads). `_process_page` sees neither, and
correctly so — it is a per-page function. The fix is a document-level assembly
pass between those two line numbers, not more work inside the page loop.

Three of the four already have their domain vocabulary in place and unused:
`OCRParagraph`, `RegionType.RUNNING_HEAD`, `RegionType.FOOTNOTE`.
`RegionType` is populated only by `KrakenLayoutAnalyzer._region_type`
(`kraken.py:147`), which reads `record.category` — a field Kraken's segmenter
rarely sets. In practice every line in the book carried `RegionType.UNKNOWN`.

## 2. The faithfulness constraint, and how to satisfy it

CLAUDE.md rule 1: no stage may silently alter recognized text. Joining a
hyphen alters recognized text. This is a real conflict, not a technicality.

**Resolution: paragraphs are a view, never a rewrite.** The assembly stage
must not mutate `OCRLine.text`. It emits `OCRParagraph` objects that *reference*
lines and carry the assembled rendering alongside them. Then:

- **ALTO / PAGE-XML** keep emitting lines verbatim — archival output is
  bit-identical to today, and remains the v2 training input.
- **DOCX / Markdown / TXT** render the assembled view.

ALTO already standardises the hyphen case: `<String SUBS_TYPE="HypPart1"`/
`"HypPart2"` with `SUBS_CONTENT` holding the joined word. The format was
designed for exactly this, so the join is recorded and reversible by
construction rather than by a convention we invent. Use it.

## 3. Hyphenation — 850 line-ends (29%)

**Reframe first: 29% is not an error rate.** Greek hyphenates syllabically at
line ends, and this book is set in a narrow measure. Roughly a third of lines
ending in a hyphen is the *expected* incidence for the material. The defect is
that nothing joins them, not that they occur.

Detect: line ends `-` (U+002D), `‐` (U+2010), `¬`, or U+00AD; a next line
exists in the same column; the next line begins lowercase.

The hard case is undecidable from geometry alone: a real compound
(`Ἑλληνο-γαλλικός`) broken at its own hyphen is indistinguishable from a
syllabic break. So decide lexically, using the lexicons `SuggestOnlyCorrector`
already loads via `lexicons_by_script()`:

| `AB` in lexicon | `A-B` in lexicon | Action |
|---|---|---|
| yes | no | join, drop hyphen |
| — | yes | join, keep hyphen |
| no | no | join, drop hyphen (syllabic breaks dominate in Greek) **and emit a `Suggestion`** |

Never join silently across a page or column boundary — flag those. The
`Suggestion` path means every ambiguous join is visible in the review UI
(`infrastructure/review.py`) rather than decided behind the user's back.

## 4. Paragraph reconstruction

Every signal needed is already on `OCRLine.bbox` and `.reading_order`. No new
extraction, no new dependency.

Per page, derive the column geometry: left edge = mode of `bbox.x` over body
lines; right edge = max `bbox.right`; median leading = median of
`next.bbox.y − cur.bbox.bottom`.

Break a paragraph when any holds:

- **indent** — `next.bbox.x > left_edge + 0.02 × column_width`
- **short line then flush line** — `cur.bbox.right < right_edge − 0.15 × column_width`
  and the next line starts at the left edge
- **gap** — vertical gap `> 1.5 × median_leading`

Greek trade books indent the first line, so indent is the primary signal and
the other two are backstops. Across a page break, continue the paragraph unless
the last line of page *N* is short.

This is standard practice — line splitting followed by line clustering, keyed
on baseline spacing and alignment — and it is what eynollah and the OCR4all
workflow do heuristically before any learned model is involved.

## 5. Running heads and page numbers

Two detectors. The geometric one is cheap and weak; the repetition one is the
one that actually works.

**Geometric (per page):** first/last line separated from the body block by more
than the median leading, and short.

**Repetition (document-level):** the Internet Archive's `find_header_footer.py`
recipe, and the reason this must be a document-level pass. Take the top-*K* and
bottom-*K* lines of every page, lowercase them, then **replace every digit run
and Roman numeral with a sentinel `@`**. That single normalisation is the whole
trick: a page number differs on every page and would never match, but `@`
collapses the varying part and lets the surrounding running head match its
neighbours. Fuzzy-match each candidate against the same slot on adjacent pages
with a ±4-character length tolerance to absorb OCR noise; classify as a running
head at a match score ≥ 0.9.

**Mark, do not delete.** Set `region_type = RegionType.RUNNING_HEAD` and let
each exporter decide — DOCX to a real Word header or dropped, ALTO retained as
a region. Deleting recognized text in the pipeline would violate rule 1; the
enum value exists precisely so the decision can live at the edge.

## 6. Styles

`DocxExporter` writes every line as a `Normal` paragraph
(`exporters.py:310`). Two inputs give it more:

- `region_type` from §5 → `Header`, `Footnote Text`
- **type size proxy**: `bbox.h` relative to the page median line height. A line
  ≥ 1.3× median, short, and centred (`|left margin − right margin| < 0.1 ×
  column_width`) is a heading.

Map role → Word style via `add_paragraph(style=...)` / `add_heading` — already
in `python-docx`, no new dependency. Height-as-size is a proxy and will misfire
on all-caps lines; that is acceptable for a style hint the reviewer can see and
override, and it is the only size signal Tesseract's `image_to_data` and
Kraken's polygons both give.

## 7. Build order

One new module, `application/structure.py`, plus a port. Ordered by
value-over-risk:

1. **`RunningHeadDetector`** — document-level, marks only, cannot corrupt text.
   Biggest visible improvement per unit of risk.
2. **`ParagraphAssembler`** → emits the already-defined `OCRParagraph`. Fixes §4
   and creates the place where §3 lives.
3. **`Dehyphenator`** inside the assembler, lexicon-gated, `Suggestion` on
   ambiguity.
4. **Exporter role → style mapping.** Trivial once 1–3 exist.

## 8. Honest limits

These are heuristics and they will misfire — on pages with figures, on tables,
on a title page whose geometry resembles nothing else in the book. That is
tolerable *because* the mistakes are marks and views rather than edits: the
lines survive untouched underneath, ALTO output is unaffected, and the review
UI shows every ambiguous decision. A structure pass that mutated `OCRLine.text`
would have no such floor, which is the argument for building it as a view even
though a view costs more.

## Sources

- [OCR-D: an end-to-end open source OCR framework for historical printed documents](https://dl.acm.org/doi/epdf/10.1145/3322905.3322917)
- [OCR4all — a (semi-)automatic OCR workflow for historical printings](https://arxiv.org/pdf/1909.04032)
- [Improved dehyphenation of line breaks for PDF text extraction](https://ad-publications.cs.uni-freiburg.de/theses/Bachelor_Mari_Hernaes_2019.pdf)
- [internetarchive/analyze_ocr — `find_header_footer.py`](https://github.com/internetarchive/analyze_ocr/blob/master/find_header_footer.py)
- [Header and footer extraction by page-association](https://www.researchgate.net/publication/221253782_Header_and_Footer_Extraction_by_Page-Association)
- [Post-OCR paragraph recognition by graph convolutional networks](https://arxiv.org/pdf/2101.12741)
- [qurator-spk/eynollah — document layout analysis](https://github.com/qurator-spk/eynollah)
