# Implementation plan — document structure recovery

Companion to [DOCUMENT_STRUCTURE.md](DOCUMENT_STRUCTURE.md), which argues *what*
to build and why. This document is *how*: modules, signatures, paradigms,
phase order, and the test that proves each phase.

---

## 0. The constraint that shapes everything

`tests/test_faithfulness_pipeline.py::test_text_survives_every_export_byte_identical`
is parametrized over `PlainTextExporter`, `MarkdownExporter`, `AltoXmlExporter`
and `PageXmlExporter`, and asserts the engine's exact bytes appear in the
exported output. A stage that joins hyphens necessarily breaks it for TXT and
Markdown.

**Therefore: assembly is opt-in and the default is identity.** The existing test
constructs `PipelineOrchestrator` without an assembler, so the archival path is
unchanged by construction and that test must keep passing untouched. Weakening
it would be the wrong move — it is the product's central guarantee.

Two consequences that run through every decision below:

1. **`OCRLine.text` is never rewritten.** Paragraphs are a parallel view.
2. **Every join is recorded well enough to be undone.** Not as a comment — as
   data, verified by a round-trip property test.

---

## 1. Domain additions — `domain/models.py`

Paradigm: **immutable value objects**, matching every existing model (frozen,
`slots=True`). All new fields carry defaults, so every existing construction
site and test keeps compiling.

```python
class ParagraphRole(str, Enum):
    BODY = "body"
    HEADING = "heading"
    SUBHEADING = "subheading"
    RUNNING_HEAD = "running_head"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"


@dataclass(frozen=True, slots=True)
class LineJoin:
    """How two consecutive lines were joined, recorded so it can be undone."""

    first_line_id: str
    second_line_id: str
    separator: str        # "" hyphen dropped | "-" hyphen kept | " " plain wrap
    removed: str          # the exact character removed from line one, "" if none
    verdict: str          # joined_in_lexicon | hyphen_in_lexicon | unverified


@dataclass(frozen=True, slots=True)
class OCRParagraph:          # extends the existing, currently-unused model
    id: str
    lines: Tuple[OCRLine, ...] = field(default_factory=tuple)
    text: str = ""
    role: ParagraphRole = ParagraphRole.BODY
    joins: Tuple[LineJoin, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    ...                       # unchanged fields
    paragraphs: Tuple[OCRParagraph, ...] = field(default_factory=tuple)
```

`DocumentPage.paragraphs` defaulting to empty is what keeps this additive: ALTO
and PAGE-XML ignore it forever; DOCX/MD/TXT render paragraphs *when present* and
fall back to `lines` when not. No exporter signature changes, no new exporter
port, and the faithfulness test — which produces no paragraphs — is unaffected.

`LineJoin.removed` storing the *exact* character rather than a boolean is what
makes the round-trip exact across `-`, `‐`, `¬` and U+00AD.

---

## 2. Port — `ports/interfaces.py`

```python
class IDocumentAssembler(Protocol):
    def assemble(
        self, document: DocumentStructure, context: TenantContext
    ) -> Result[DocumentStructure, LayoutError]: ...
```

Paradigm: **structural typing via `Protocol`**, identical to every existing
port. `DocumentStructure → DocumentStructure` (endomorphic) rather than
`→ Sequence[OCRParagraph]`, for three reasons: assemblers compose by
function composition; the identity assembler is a one-liner; and the running-head
detector needs to rewrite `region_type` on lines, which a paragraph-only return
could not express.

`Result[..., LayoutError]` reuses the existing error type — structure recovery
is layout inference, and inventing a `StructureError` would add a domain type
that no caller distinguishes.

---

## 3. Modules — `application/structure/`

A package, not a module: five concerns with genuinely different shapes, and
`application/` is already flat with twelve files.

### 3.1 `geometry.py` — pure functions

```python
def column_left_edge(lines: Sequence[OCRLine]) -> int
def column_right_edge(lines: Sequence[OCRLine]) -> int
def median_leading(lines: Sequence[OCRLine]) -> float
def median_line_height(lines: Sequence[OCRLine]) -> float
def is_centred(line: OCRLine, left: int, right: int, tolerance: float) -> bool
```

Paradigm: **pure functional**. No state, no I/O, no classes. Every input is a
`Sequence[OCRLine]`, every output a number. This is the layer where bugs hide
(off-by-one on `bbox.right`, empty-sequence division), so it is the layer that
must be trivially testable and property-testable.

Pattern: none. A class here would be ceremony around four `statistics.median`
calls. `statistics` is stdlib — no numpy dependency for medians over ~40 values.

**Left edge is the mode, not the minimum** — the minimum is whatever hanging
indent or stray marginal noise sits furthest left, which is exactly the wrong
answer. Bucket `bbox.x` to the nearest few pixels before taking the mode, since
raw pixel values will not repeat.

### 3.2 `breaks.py` — paragraph break rules

```python
class BreakRule(Protocol):
    name: str
    def breaks_before(self, previous: OCRLine, current: OCRLine, page: PageGeometry) -> bool: ...


class IndentRule:      ...   # current.bbox.x > left + 0.02 * width
class ShortLineRule:   ...   # previous.bbox.right < right - 0.15 * width
class GapRule:         ...   # gap > 1.5 * median_leading
```

Pattern: **Strategy**, composed by `any()` over an injected tuple of rules.

Justified, not reflexive: the three signals are independent, they need
*different thresholds for different books*, and a Greek trade book set with
first-line indents wants `IndentRule` weighted differently than a critical
edition set flush. Inlining them as three `if`s in the assembler would make
per-book tuning a code edit and make each rule untestable in isolation. This is
the one place in the plan where a pattern earns its keep — everywhere else,
plain functions.

`PageGeometry` is a small frozen dataclass computed once per page from §3.1 and
passed down, so no rule recomputes a median.

### 3.3 `hyphenation.py` — the dehyphenator

```python
@dataclass(frozen=True)
class Dehyphenator:
    lexicons: Mapping[Script, ILexicon]

    def join(self, first: OCRLine, second: OCRLine) -> LineJoin | None: ...
```

Pattern: **Policy object** with an injected `ILexicon` — the port already exists
and `lexicons_by_script()` already builds the mapping for
`SuggestOnlyCorrector`. Reuse it; do not add a lexicon loader.

Returns `LineJoin | None` rather than a joined string: the decision and its
evidence are the return value, and the caller performs the concatenation. That
keeps the policy pure and makes the decision table directly assertable.

Decision table (from DOCUMENT_STRUCTURE.md §3), implemented as an explicit
`match`/`if` ladder, not a lookup — there are three branches and a lookup table
would obscure them:

| `AB` in lexicon | `A-B` in lexicon | separator | verdict |
|---|---|---|---|
| yes | no  | `""` | `joined_in_lexicon` |
| —   | yes | `"-"` | `hyphen_in_lexicon` |
| no  | no  | `""` | `unverified` → also emit a `Suggestion` |

Returns `None` — refusing to join — when the lines are on different pages or
different columns. Cross-boundary joins are where a wrong guess is least
recoverable and least visible.

### 3.4 `running_heads.py` — document-level detection

```python
def normalize_candidate(text: str) -> str          # lowercase; digit runs and Roman numerals -> "@"
def detect(document: DocumentStructure, *, top_k: int = 2, bottom_k: int = 2,
           window: int = 2, threshold: float = 0.9) -> Mapping[str, RegionType]
```

Paradigm: **two-phase map/reduce over the whole document**, as a pure function.
Map: normalize every page's top-*k* and bottom-*k* lines. Reduce: score each
candidate against the same slot on the ±`window` neighbouring pages and keep
those scoring ≥ `threshold`.

Returns a `Mapping[line_id, RegionType]` — a *decision*, not a mutated document.
The assembler applies it via `dataclasses.replace`. This keeps the detector
testable with hand-built pages and no exporter in sight.

The `@` normalization is the whole algorithm and deserves its own test: a page
number differs on every page and can never match its neighbours until the digits
collapse to a sentinel. Roman numerals matter here specifically — Greek
front-matter is paginated in them.

Similarity: `difflib.SequenceMatcher.ratio()` — stdlib, adequate at this length,
and avoids a `rapidfuzz` dependency for a few hundred comparisons. Length
tolerance ±4 characters absorbs OCR noise before the ratio is computed.

Complexity is `O(pages × k × window)` — linear in pages with `k=2, window=2`.

### 3.5 `roles.py` — heading and role classification

```python
def classify(paragraph_lines: Sequence[OCRLine], page: PageGeometry) -> ParagraphRole
```

Pure function. Heading when height ≥ `1.3 × median_line_height`, short, and
centred. Running head and page number come from §3.4 and win over geometry.

`bbox.h` as a type-size proxy is a **deliberate simplification with a known
ceiling**: it misfires on all-caps lines and on lines with tall ascenders. It is
the only size signal both Tesseract's `image_to_data` and Kraken's polygons
provide without re-reading the page image. Mark it in-code:
`# ponytail: bbox height as type-size proxy; read real font metrics if headings misfire`.

### 3.6 `assembler.py` — composition

```python
class IdentityAssembler:      # the default; returns the document unchanged
class DocumentAssembler:      # composes 3.1–3.5
```

Pattern: **Pipeline** — the same composition style as `FallbackLayoutAnalyzer`,
which the codebase already uses. Order matters and is fixed: running heads →
paragraph grouping → dehyphenation → role classification. Running heads must run
first so head lines never get absorbed into a body paragraph.

`IdentityAssembler` existing as a named class rather than `None` means the
pipeline has no `if assembler is not None` branch, and the opt-in path is a
composition-root decision instead of a runtime one.

---

## 4. Exporters — `infrastructure/exporters.py`

Change is confined to the three rendering exporters. ALTO and PAGE-XML are
**not touched**.

```python
_STYLE_BY_ROLE: Mapping[ParagraphRole, str] = {
    ParagraphRole.HEADING: "Heading 1",
    ParagraphRole.SUBHEADING: "Heading 2",
    ParagraphRole.FOOTNOTE: "Footnote Text",
    ParagraphRole.RUNNING_HEAD: "Header",
    ParagraphRole.BODY: "Normal",
}
```

A module-level mapping with `.get(role, "Normal")`, not a Strategy class — it is
data, it has one consumer, and a class would be an abstraction over a dict.
`python-docx` supplies `add_paragraph(style=...)`; no new dependency.

Each exporter reads `page.paragraphs` when non-empty and `page.lines` otherwise.
That fallback is what makes the whole change additive.

**Searchable PDF is unchanged.** Its text layer must stay word-box-grounded
against the page image; paragraph assembly has no meaning there and joining
would misalign the invisible layer.

---

## 5. Composition and CLI

`composition/desktop.py` gains a parameter, defaulted off:

```python
def create_ensemble_pipeline(..., assemble_structure: bool = False) -> PipelineOrchestrator:
    assembler = DocumentAssembler(...) if assemble_structure else IdentityAssembler()
```

`interfaces/cli.py` gains `run.add_argument("--structure", action="store_true",
help="reconstruct paragraphs, join hyphens, and mark running heads")`.

Off by default. The archival path stays byte-identical unless the user asks for
a reading copy.

---

## 6. Test plan

Project rule is tests first. Per phase:

| Phase | Test file | The test that matters |
|---|---|---|
| P1 | `test_structure_identity.py` | `IdentityAssembler` returns a document equal to its input; full existing suite stays green |
| P2 | `test_running_heads.py` | page numbers differing on every page still match once normalized; a real body line never scores ≥ threshold |
| P3 | `test_paragraphs.py` | each `BreakRule` in isolation; assembled paragraph count on a hand-built page |
| P4 | `test_hyphenation.py` | the three-row decision table; **round-trip property** |
| P5 | `test_exporters.py` (extend) | role → style mapping; empty `paragraphs` still renders from `lines` |
| P6 | `test_cli.py` (extend) | `--structure` off by default |

**The round-trip property is the centrepiece.** With `hypothesis`, already in
the suite:

```python
@given(lines=st.lists(line_strategy(), min_size=2))
def test_joins_are_exactly_reversible(lines):
    paragraph = assemble(lines)
    assert unjoin(paragraph) == tuple(line.text for line in lines)
```

`unjoin` reconstructs the original line texts from `paragraph.text` and
`paragraph.joins` alone. If it holds for all generated inputs, faithfulness
under assembly is a machine-checked invariant rather than a claim in a doc.
Write `unjoin` in the same module as the assembler and export it — it is also
what a future "revert this join" button in the review UI calls.

Add one test asserting the running-head detector changes `region_type` and
leaves `text` byte-identical.

---

## 7. Phase order

Each phase is one PR, green before the next starts.

1. **P1 — walking skeleton.** Domain fields, port, `IdentityAssembler`, wiring,
   CLI flag. *Zero behaviour change.* Proves the seam is additive before any
   heuristic exists. Small and boring on purpose.
2. **P2 — running heads.** Marks only, cannot corrupt text. Best
   visible-improvement-per-risk in the set.
3. **P3 — paragraphs.** Emits the model that has been defined and unused since
   the domain was written.
4. **P4 — dehyphenation.** Last of the text-affecting work, because it depends
   on P3's grouping and carries the most risk.
5. **P5 — exporter styles.** Trivial once roles exist.
6. **P6 — review UI surfaces joins and roles.** `infrastructure/review.py`
   already has a per-line view model; add the paragraph view so ambiguous joins
   are visible where corrections are already made.

---

## 8. Optimization — parallel page processing

Separate from structure, and the largest real win available: the 74-page book
took ~23 hours. `PipelineOrchestrator._process_page` is already independent per
page — its engine cache is a page-local dict and it touches no shared state.

Use `concurrent.futures.ProcessPoolExecutor` over pages, not threads: Tesseract
via `pytesseract` shells out and Kraken's torch path holds the GIL for
meaningful stretches, so threads would under-deliver. Pages are independent, and
results are reordered by `page.number` on collection.

Gates this must not break: `PipelineEvent` ordering (emit on collection, not
completion), and the `--json` page-failure report. Default worker count
`os.cpu_count() - 1`, overridable by `--workers`, default `1` until measured —
an unmeasured parallel default is how a working CPU-only tool starts thrashing
on a laptop.

Do this **after** P1–P5. Parallelizing a pipeline whose output is still changing
means debugging two things at once.

---

## 9. Known ceilings, stated rather than hidden

- **Whole document in memory.** The assembler needs cross-page context, so
  page-streaming ingest cannot extend through it. Fine at 74 pages; at 1000+ the
  running-head detector should run over a page-summary projection rather than
  full `DocumentStructure`. Mark, do not pre-build.
- **Heuristics misfire** on figures, tables, and title pages. Tolerable only
  because they mark and view rather than edit, and the review UI shows them.
- **`bbox.h` as type size** — §3.5.
- **Single-column assumption.** The column geometry in §3.1 assumes one text
  column. Multi-column pages will produce wrong paragraph breaks. Detecting
  columns is a genuinely larger problem; the honest move is to detect *that*
  there are probably two columns (bimodal `bbox.x` distribution) and skip
  assembly for that page rather than assemble it wrongly.

---

## 10. Outside this plan, still outstanding

Listed so they are not silently dropped; none are prerequisites.

- **F-2** — real scans with diplomatic ground truth in `tests/corpus/`
- **F-8** — real lexicons (Hunspell `el_GR`, CLTK) behind the existing `ILexicon`
- **E-3** — metrics export via `prometheus-client`
- `review_ui.py` view-model split
- Decision on deleting `prototype/ocr.py` now that the package pipeline is ahead
  of it on every axis
