# OmniOCR UX/UI Optimization Plan

**Date:** 2026-07-27
**Status:** Living document — update after each iteration

---

## 1. Current State Assessment

The OmniOCR Desktop Edition runs a Streamlit-based review UI at `editions/desktop/review_ui.py`. After the first optimization pass (commit `a158538`), it provides:

| Feature | Status |
|---|---|
| PDF upload (up to 2 GB) | ✅ |
| OCR pipeline (Kraken layout + Tesseract/Kraken recognition) | ✅ |
| Confidence heatmap (🔴🟡🟢✅ per line) | ✅ |
| Inline line editing (✏️ expand → text area) | ✅ |
| Page navigation (number input + arrow buttons) | ✅ |
| Polytonic keyboard (persistent sidebar panel) | ✅ |
| Suggestion display (expander per line) | ✅ |
| Accept suggestion → ground truth | ✅ |
| Markdown / Text export download | ✅ |
| Document info (file name, pages, failures) | ✅ |
| Keyboard shortcuts (← →) | ⚠️ Basic Arrow keys only |
| VLM reviewer (opt-in via env var) | ✅ |
| Progress indicator during OCR | ✅ |

### Unresolved critical gaps

1. **No real-time progress during OCR.** The progress bar only shows "spinner → done". For a 500-page book, the user sees no incremental progress for 5–10 minutes.

2. **Export is incomplete.** Only Markdown and plain text. No DOCX, PDF, or ALTO/PAGE-XML download from the UI.

3. **No batch operations.** User must accept each suggestion individually. No "Accept all" or "Accept all reversible".

4. **No undo.** Cannot revert an accepted suggestion or an edit. No undo stack.

5. **Exit/resume not implemented.** If the user closes the browser, all ground truth is lost. No session persistence.

6. **Polytonic keyboard inserts via click → clipboard workflow.** Cannot click a glyph to directly append it to the currently-focused text area. User must manually paste.

7. **No search.** Cannot search for text across pages (e.g., find all occurrences of a word, or jump to a page by text content).

8. **No comparison view.** Cannot see the same page with different engines side by side to compare disagreements.

9. **No dashboard/overview.** No summary view of the entire book — total suggestions, worst pages by confidence, CER estimates.

10. **Streamlit-specific limitations.** Reruns on every interaction (slow for large documents). No drag-and-drop text editing. No native rich text.

---

## 2. Optimization Goals by Priority

### P0 — Must fix (blocks real-world use)

| Goal | Why | Effort |
|---|---|---|
| Real-time incremental progress | User needs to know the pipeline is still working on page N of M | Medium |
| Session persistence (save/load) | User closes browser → loses all ground truth | High |
| Full export (DOCX, PDF, ALTO) | Scholar needs to submit work in specific formats | Medium |
| "Accept all" suggestions | 500-page book → 2000 suggestions → clicking each is impossible | Low |

### P1 — High impact, manageable effort

| Goal | Why | Effort |
|---|---|---|
| Undo/redo stack | Fear of making mistakes blocks use | Medium |
| Click-to-edit (not expander) | Expanding and collapsing per line is slow for many lines | Medium |
| Text search across pages | Finding a passage in a multi-volume set | Medium |
| Dashboard overview | Summary before diving into review | Low |
| Page-level skip/accept | Skip pages with zero low-confidence lines | Low |

### P2 — Nice to have (sophistication layer)

| Goal | Why | Effort |
|---|---|---|
| Engine comparison view | See where Tesseract and Kraken disagree | High |
| Reading order visualization | Understanding critical edition layout | Medium |
| Drag-and-drop line reordering | Correcting reading order in apparatus/scholia | High |
| Dark mode | Accessibility, long sessions | Low |
| PDF page export with highlighted OCR | Scholar wants a PDF with overlays and corrections | High |

---

## 3. Detailed Design Proposals

### 3.1 Real-time incremental progress (P0)

**Problem:** `pipeline.run()` is synchronous — it calls the engine for each page in a loop. The Streamlit thread blocks, so no updates reach the UI until `run()` returns.

**Proposed solution:** Use the existing `IEventBus` infrastructure. The pipeline already publishes `PipelineEvent("page_completed", N)` and `PipelineEvent("page_failed", N)` via `InMemoryEventBus`. Wire the Streamlit UI to subscribe to these events.

Architecture:
```python
# In the Streamlit UI:
bus = InMemoryEventBus()
completed_pages = []

def on_page_event(event):
    completed_pages.append(event.page_number)
    # Streamlit doesn't support async updates from background threads,
    # so we need to poll in the main thread.

bus.subscribe(on_page_event)

pipeline = PipelineOrchestrator(event_bus=bus, ...)
pipeline.run(pdf_bytes, ctx)  # still runs synchronously
```

**Limitation:** Streamlit's architecture doesn't support background-thread UI updates. We can't stream progress without moving the OCR to a background process. Two paths:

**Path A — Streamlit-compatible (doable now):**
Pre-run the pipeline to count total pages, then run page-by-page in a loop from the Streamlit thread, calling `pipeline._process_page()` directly for each page. This lets us yield to Streamlit's event loop after each page.

**Path B — Background worker (future):**
Move OCR to a subprocess/RQ worker, poll for progress via `st.status()` or `st.progress()`.

**Recommendation:** Path A for immediate implementation. Use `pipeline._process_page()` in a page-by-page loop with `st.progress()` updates.

### 3.2 Session persistence (P0)

**Problem:** All state is in `st.session_state` — in-memory only. Browser close = data loss.

**Proposed solution:** Two-tier persistence:

1. **Auto-save** — after every page change, write ground truth + edits to a JSON file on disk (`~/.omniocr/sessions/[hash].json`)
2. **Load on startup** — if the same file is uploaded again (hash match), offer to restore the session

Storage format:
```json
{
  "file_hash": "sha256-of-uploaded-file",
  "file_name": "book.pdf",
  "created": "2026-07-27T16:00:00",
  "last_modified": "2026-07-27T17:30:00",
  "current_page": 42,
  "ground_truth_lines": {
    "line-1": "corrected text here"
  },
  "edited_lines": {
    "line-2": "manually edited text"
  },
  "accepted_suggestions": [...]
}
```

**Effort:** ~100 lines of Python. Uses stdlib `json` and `hashlib`.

### 3.3 Full export (P0)

**Problem:** Only Markdown and TXT exports are available from the UI.

**Proposed solution:** Add download buttons for all supported formats:

```python
# Sidebar export section
st.sidebar.subheader("Export")
formats = {
    "Markdown (.md)": MarkdownExporter,
    "Plain Text (.txt)": PlainTextExporter,
    "ALTO XML (.xml)": AltoXmlExporter,
    "PAGE-XML (.xml)": PageXmlExporter,
    "DOCX (.docx)": DocxExporter,
    "Searchable PDF (.pdf)": SearchablePdfExporter,
}
for label, exporter_cls in formats.items():
    if st.button(f"Export {label}"):
        exporter = exporter_cls(...)
        result = exporter.export(document, ctx)
        st.download_button("Download", result.value, ...)
```

**Consideration:** `DocxExporter` requires `python-docx`, `SearchablePdfExporter` requires `PyMuPDF` and a font path. The UI should check for optional deps and show a "install with pip" hint if missing.

### 3.4 "Accept all" suggestions (P0)

**Problem:** Manual acceptance of every suggestion is infeasible for large documents.

**Proposed solution:** Add batch operations in the sidebar:

```python
# Suggestion summary section
st.sidebar.subheader("Batch Actions")
if st.button("Accept all reversible suggestions"):
    for page in doc.pages:
        for line in page.lines:
            for s in line.suggestions:
                if s.reversible and s.suggestion_text:
                    st.session_state.ground_truth_lines[s.line_id] = s.suggestion_text
    st.rerun()

if st.button("Accept all NFC normalizations"):
    # Only accept unicode_nfc suggestions — safest
    ...

if st.button("Reject all suggestions"):
    st.session_state.ground_truth_lines.clear()
    st.rerun()
```

**Safety:** "Accept all reversible" is safe because reversible suggestions (NFC normalization, ligature expansion, abbreviation expansion) never alter meaning. Non-reversible suggestions (lexicon flags, diacritic violations) require human review.

### 3.5 Undo/redo stack (P1)

**Problem:** No way to revert a mistake.

**Proposed solution:** Maintain an undo stack in `st.session_state`:

```python
if "undo_stack" not in st.session_state:
    st.session_state.undo_stack: list[tuple[str, str, str]] = []
    # (action, line_id, previous_text)

def push_undo(line_id: str, old_text: str):
    st.session_state.undo_stack.append(("edit", line_id, old_text))

def undo():
    if st.session_state.undo_stack:
        action, line_id, text = st.session_state.undo_stack.pop()
        if action == "edit":
            st.session_state.ground_truth_lines[line_id] = text
        elif action == "accept":
            st.session_state.ground_truth_lines.pop(line_id, None)
        st.rerun()
```

Add an "Undo" button in the sidebar. Stack grows linearly with edits — memory concern is minimal (< 1 MB for a 500-page book).

### 3.6 Click-to-edit (P1)

**Problem:** Per-line expander requires clicking ✏️ → editing → collapsing. Too many clicks.

**Proposed solution:** Make the line text itself a clickable target that opens an inline editor. Streamlit doesn't support inline click-to-edit natively, but we can:

1. **Use `st.form` per line** — cleaner than expander, but still requires a submit button
2. **Use custom HTML with `contenteditable`** and Streamlit's `st.components.v1.html` — most natural UX, but requires JavaScript bridging
3. **Use a global edit mode toggle** — switch between "view" and "edit" modes for the current page

**Recommendation:** Implement a global "Edit Mode" toggle in the sidebar. When enabled, every line becomes a text input instead of styled text. This is the simplest Streamlit-native approach and scales well.

### 3.7 Text search (P1)

**Problem:** Cannot find text across pages.

**Proposed solution:** Add a search bar in the sidebar:

```python
query = st.sidebar.text_input("Search")
if query:
    results = []
    for page in doc.pages:
        for line in page.lines:
            text = st.session_state.ground_truth_lines.get(line.line_id, line.text)
            if query.lower() in text.lower():
                results.append((page.number, line.line_id, text))
    st.sidebar.write(f"Found {len(results)} matches")
    for page_num, line_id, text in results[:20]:
        if st.sidebar.button(f"p.{page_num}: {text[:60]}…"):
            st.session_state.current_page = page_num
            st.rerun()
```

### 3.8 Engine comparison view (P2)

**Problem:** Can't see where Tesseract and Kraken disagree.

**Proposed solution:** The pipeline's reconciliation step compares candidates from multiple engines. The `OCRLine.provenance` and `OCRLine.blocks` fields already contain per-engine results. Expose this in a comparison grid:

```
Line #1:  [Tesseract: "ἄνθρωπος" 72%]  [Kraken: "ἄνθρωπος" 94%]  → Kraken wins
Line #2:  [Tesseract: "λόγος" 85%]    [Kraken: "λόγοσ" 91%]     → ⚠️ Disagreement
```

**Effort:** ~300 lines. Need to extract per-engine blocks from `OCRLine.blocks` and render a comparison table. High impact for the critical-edition use case.

### 3.9 Dashboard (P1)

**Problem:** No overview before diving into review.

**Proposed solution:** Add a "Dashboard" tab/view before the page review:

```python
tab1, tab2 = st.tabs(["📊 Dashboard", "📄 Review"])

with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Pages", total_pages)
    col2.metric("Failed Pages", failed_pages)
    col3.metric("Suggestions", total_suggestions)
    col4.metric("Low-conf Lines", sum(1 for p in doc.pages for l in p.lines if l.is_low_confidence))

    # Worst pages by average confidence
    st.subheader("Pages by Average Confidence")
    page_scores = [
        (p.number, avg_confidence(p), len([l for l in p.lines if l.is_low_confidence]))
        for p in doc.pages
    ]
    # Bar chart of page scores
    st.bar_chart(...)

    # Suggestion breakdown
    st.subheader("Suggestions by Type")
    st.bar_chart(group_suggestions_by_reason(doc))
```

---

## 4. Implementation Roadmap

### Sprint 1: P0 critical fixes (~2 sessions)

| Task | Estimated lines | Dependencies |
|---|---|---|
| 3.1 Real-time progress | ~80 lines | IEventBus wired in UI |
| 3.2 Session persistence | ~120 lines | stdlib json/hashlib |
| 3.3 Full export | ~100 lines | exporters.py (already built) |
| 3.4 Accept all suggestions | ~60 lines | None |

### Sprint 2: P1 high-impact (~2 sessions)

| Task | Estimated lines | Dependencies |
|---|---|---|
| 3.5 Undo/redo | ~80 lines | Session persistence |
| 3.6 Edit mode toggle | ~100 lines | None |
| 3.7 Text search | ~80 lines | None |
| 3.9 Dashboard | ~150 lines | group_suggestions_by_reason() |

### Sprint 3: P2 sophistication (~3 sessions)

| Task | Estimated lines | Dependencies |
|---|---|---|
| 3.8 Engine comparison view | ~300 lines | OCRLine.blocks extraction |
| Reading order visualization | ~200 lines | BBox rendering |
| Dark mode | ~30 lines | Streamlit theme config |
| PDF overlay export | ~250 lines | SearchablePdfExporter |

---

## 5. Architecture Notes [AUDITED — see §7 for corrections]

### Streamlit constraints to remember

1. **No background thread UI updates.** All UI changes must happen in the main thread during a rerun cycle.
2. **Every interaction triggers a full rerun.** Heavy computation in `st.session_state` callbacks is bad. Precompute data once and cache it.
3. **`st.cache_data` for expensive operations.** Pipeline results, image decoding — cache them.
4. **Custom HTML with `st.components.v1.html`** bridges the gap where native widgets fall short (drag-and-drop, contenteditable, canvas overlays).
5. **`st.dataframe` for tabular data** — better than writing loops of `st.write()` for suggestion lists or search results.

### Clean architecture reminder

The review UI is thin. All business logic lives in `packages/omniocr/src/omniocr/`. The UI only:
- Calls `PipelineOrchestrator` and exporters
- Stores state in `st.session_state`
- Maps domain models to display widgets

No engine calls, no OpenCV, no OCR logic in the UI. Maintain this separation.

---

## 6. Metrics for Success

| Metric | Current | Target |
|---|---|---|
| Time to review 1 page (300 lines) | ~3 minutes | < 1 minute |
| Pages reviewed before fatigue | ~20 | > 100 |
| Suggestion acceptance rate | Manual, one-by-one | Batch-accept with undo |
| Export formats | 2 (Markdown, TXT) | 6 (all formats) |
| Session persistence | None | Auto-save every page change |
| First-time user confusion | High ("what do I do?") | Dashboard guides first step |

---

## 7. Architecture Compliance Audit

> Cross-referenced against `docs/ARCHITECTURE.md`, `AGENTS.md`, `docs/BUILD_PLAN.md`.

### Principle: "Keep business logic in the shared package, not in edition controllers" (AGENTS.md)

| Proposal | Verdict | Issue |
|---|---|---|
| 3.1 Path A — `pipeline._process_page()` from UI | ❌ **VIOLATION** | Calls a private pipeline method from the Streamlit UI. The pipeline's internal page processing is an implementation detail that editions must not depend on. |
| 3.1 Path B — Background worker | ✅ OK | Moves OCR to a subprocess; UI polls for status. No layer violation. |
| 3.2 Session persistence | ✅ OK | UI-layer state management. Uses stdlib JSON. No business logic moved to UI. |
| 3.3 Full export | ✅ OK | Exporters are infrastructure adapters. Calling them from the edition composition root is correct. |
| 3.4 Accept all suggestions | ✅ OK | Iterates over domain models (read-only) and mutates UI session state only. No domain mutation. |
| 3.5 Undo/redo | ✅ OK | Pure UI concern — manages `st.session_state` stack. |
| 3.6 Edit mode toggle | ✅ OK | Pure UI display mode. |
| 3.7 Text search | ✅ OK | Read-only access to domain models via `doc.pages[X].lines[Y].text`. |
| 3.8 Engine comparison | ✅ OK | Reads `OCRLine.blocks` and `OCRLine.provenance` — both are public domain fields already populated by the pipeline. |
| 3.9 Dashboard | ✅ OK | Uses `group_suggestions_by_reason()` from `infrastructure/review.py` — a public function designed for the UI layer. |

### Principle: "No engine/OpenCV/DB calls in UI or API controllers" (ARCHITECTURE.md §9)

| Proposal | Verdict | Issue |
|---|---|---|
| All proposals | ✅ OK | No proposal introduces engine, OpenCV, or direct DB calls in the UI. Exporters are already infrastructure adapters with proper lazy imports. |
| 3.3 SearchablePdfExporter | ✅ OK | The exporter already auto-detects system fonts. The UI does not need to locate font files. |

### Principle: "Faithfulness over fluency" (ARCHITECTURE.md §1)

| Proposal | Verdict | Issue |
|---|---|---|
| 3.4 Accept all reversible | ✅ OK | Reversible suggestions (NFC normalize, ligature expand, abbreviation expand) never alter meaning. Non-reversible (lexicon flag, diacritic violation) excluded from batch accept. |
| 3.5 Undo/redo | ✅ OK | Undo restores source text. No suggestion is written to domain state — only to `st.session_state`, which is UI-local. |
| 3.6 Edit mode | ✅ OK | Edits stored in `st.session_state.edited_lines`, not in domain models. Source text in `OCRLine.text` is never mutated. |

### Required fix for 3.1

**Violation:** Section 3.1 Path A proposes calling `pipeline._process_page()` from the UI. This violates the architecture principle that the pipeline's internal methods are private to the application layer.

**Correction — Option A (preferred):** Add a public `run_iteratively()` method to `PipelineOrchestrator` in `application/pipeline.py` that yields (page_number, DocumentPage | PageFailure) tuples. The UI calls this public method; the pipeline owns the iteration logic. No private method access.

**Correction — Option B:** Use the existing `IEventBus`. The pipeline already publishes `PipelineEvent("page_completed", N)` for each page. The UI subscribes and polls.

### Updated 3.1: Architecture-compliant progress

```python
# In the Streamlit UI:
page_count = pipeline.count_pages(pdf_bytes)  # new public method on PipelineOrchestrator
for page_number, page_or_failure in pipeline.run_iteratively(pdf_bytes, ctx):
    progress.progress(page_number / page_count, f"Page {page_number}/{page_count}")
    pages.append(page_or_failure)
```

### Updated roadmap — Sprint 0 (prerequisite)

| Task | Where | Lines |
|---|---|---|
| Add `run_iteratively()` to PipelineOrchestrator | `application/pipeline.py` | ~40 |
| Add `count_pages()` to PipelineOrchestrator | `application/pipeline.py` | ~10 |
| Wire progressive UI in Streamlit | `editions/desktop/review_ui.py` | ~30 |
