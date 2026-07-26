# OmniOCR — Target Architecture

Optical Character Recognition suite for **printed Greek across every variety**: modern
monotonic, polytonic, Ancient Greek (critical editions), **Byzantine printed typefaces
(ligatures)**, and **Pontian**. Ingests large PDFs and images; exports DOCX, Markdown,
searchable PDF, TXT, and ALTO/PAGE-XML.

This document is the design of record. It supersedes the aspirational description in
`GEMINI.md` where they differ.

---

## 1. Scope decisions (locked)

| Axis | Decision | Consequence |
|------|----------|-------------|
| **Hardware** | CPU-only | Recognition backbone is Tesseract + Kraken (both CPU-capable). A GPU-class VLM is **not** a core engine. |
| **Material** | Printed books & editions | Optimize print OCR + critical-edition layout. No handwriting/HTR burden in v1. "Byzantine" = printed ligatures/typefaces, not manuscript minuscule. |
| **Output** | Faithful (diplomatic) transcription | Post-correction is **suggest-only, never auto-rewrite**. Preserve polytonic, Pontian, and Byzantine forms exactly. VLM is opt-in, always grounding-checked. |
| **Primary edition** | Desktop (offline, single scholar) | Cloud/Server are later composition-root variants over the same core. |

### Guiding principle: faithfulness over fluency

For scholarly Greek, a "plausible" reading is worse than a flagged uncertain one.
Vision-language models fluently **hallucinate/normalize** — they will silently "correct"
a genuine Pontian word or a non-standard Byzantine spelling into standard Greek
(documented in *Reading or Guessing?*, arXiv 2605.27750). Therefore:

- **Verifiable engines** (Tesseract, Kraken) are the source of truth — they emit
  bounding boxes + per-token confidence, so every character is auditable and
  round-trippable to the page image.
- **VLM output is never the unaudited sole source.** It is an opt-in second opinion,
  gated behind an explicit toggle, and reconciled against box-grounded output. Divergence
  raises a review flag, it does not overwrite.
- **Post-correction suggests; the human decides.** Lexicons highlight; they do not rewrite.

---

## 2. The script difficulty gradient

No single engine covers the full range. Route each region/line to the best engine for
its script.

| Script | Difficulty | Primary engine (CPU) |
|--------|------------|----------------------|
| Modern monotonic (print) | Easy | Tesseract `ell` |
| Polytonic / Ancient (clean print) | Medium | Tesseract `grc`, Kraken |
| Ancient critical editions (dense diacritics) | Med–Hard | **Kraken + Ciaconna** (CER ~7% vs Tesseract ~13%) |
| Pontian (Greek script) | Medium **+ lexicon** | Modern engine + **custom Pontian lexicon** (recognition is Greek-script; the dialect lives in post-correction) |
| Byzantine **printed** (ligatures) | Hard | Kraken model + **ligature→Unicode table** |
| Byzantine manuscript (minuscule) | Very Hard | *Out of v1 scope* — needs HTR + training loop (v2+) |

Kraken reaches 96.2–99.5% character accuracy on printed polytonic historical text and is
the accuracy driver; Tesseract is the fast baseline and searchable-PDF box source.

---

## 3. Pipeline

```
┌───────────────────────── omniocr_core (single shared library) ─────────────────────────┐
│                                                                                          │
│  INGEST          PREPROCESS        LAYOUT + LINE SEG      SCRIPT DETECT & ROUTE           │
│  PyMuPDF         OpenCV            Kraken baseline seg;   modern | polytonic | ancient    │
│  page-stream     deskew, denoise, region classify        | byz_print | pontian | mixed   │
│  large PDFs      Sauvola binarize  (main / apparatus /            │                       │
│  + job queue     (keep grayscale   scholia / running            routes each line          │
│  (Celery/RQ),    copy for later    head), reading order          ▼                       │
│  resumable       VLM use)                 │             RECOGNITION BANK                   │
│                                           └────────────►  ├─ Tesseract  (ell/grc, boxes)  │
│                                                           ├─ Kraken     (.mlmodel, driver)│
│                                                           └─ VLM  (cloud API, opt-in,     │
│                                                                    grounding-checked)     │
│                                                                   │                       │
│                                                                   ▼                       │
│                                       RECONCILE / VOTE  (confidence-weighted; VLM vs box  │
│                                                          divergence = review flag)        │
│                                                                   │                       │
│                                                                   ▼                       │
│                                       POST-CORRECT (faithful, suggest-only)               │
│                                       NFC normalize · polytonic diacritic validation      │
│                                       (ϊ/ϋ) · ligature/abbrev expansion (reversible       │
│                                       toggle) · per-variety lexicon HIGHLIGHT             │
│                                       (el_GR / CLTK ancient / Byzantine / Pontian)        │
│                                                                   │                       │
│                                                                   ▼                       │
│                                       HUMAN REVIEW  (image-line ↔ text, disagreement      │
│                                       highlight, confidence heatmap, polytonic keyboard)  │
│                                                                   │                       │
│                                                                   ▼                       │
│                                       EXPORT: searchable PDF (overlay on original) ·      │
│                                       DOCX · Markdown · TXT · ALTO / PAGE-XML             │
│                                                                   │                       │
└───────────────────────────────────────────────────────────────── │ ─────────────────────┘
                                                                     ▼
                                   (v2, optional) corrections → TRAINING LOOP
                                   Kraken/Calamari fine-tune on specific typefaces + MLflow
```

### Stage notes

1. **Ingest** — PyMuPDF (`fitz`) streams pages at ~300 DPI as a generator; never load a
   whole book into memory. A resumable job queue (Celery in Cloud, RQ in Server, in-process
   worker in Desktop) processes pages in batches and checkpoints to disk so a 2000-page PDF
   survives interruption. This is the "large PDF" requirement.
2. **Preprocess** (`IImageProcessor`) — per-engine: Sauvola/adaptive binarization for
   Tesseract; light-touch grayscale kept for Kraken and any VLM call. Deskew, denoise,
   optional dewarp.
3. **Layout + line segmentation** — Kraken's trainable baseline segmenter (or PaddleOCR
   PP-Structure) produces ordered line polygons and classifies regions. Reading order is
   essential for critical editions (main text vs apparatus criticus vs scholia).
4. **Script detect & route** — lightweight classifier/heuristics tag each region; router
   picks the engine. Scholars can override per document.
5. **Recognition bank** (`IOCREngine` plugins) — uniform `OCRBlock`/`OCRLine` contract.
6. **Reconcile** — confidence-weighted voting; **grounding guard** rejects VLM text with no
   visual anchor.
7. **Post-correct** — see §5. Faithful = suggest-only.
8. **Human review** — corrections stored as ground truth (ALTO/PAGE) — also v2 training fuel.
9. **Export** — see §6.

---

## 4. Engine bank

| Engine | Role | Source | v1 |
|--------|------|--------|----|
| **TesseractEngine** | Fast baseline, word boxes + confidence, searchable-PDF layer | `Github/tesseract-main.zip`, `pytesseract` | ✅ |
| **KrakenEngine** | Accuracy driver for polytonic / ancient / Byzantine print; loads script-specific `.mlmodel` (Ciaconna for ancient) | `kraken` (PyPI) | ✅ |
| **PaddleEngine / EasyOCR** | Layout/detection + optional voting member | `Github/PaddleOCR-main.zip`, `Github/EasyOCR-master.zip` | layout only |
| **VLMEngine** | Opt-in second opinion + messy-layout pages; **cloud API** (Claude/Gemini) since local VLM needs GPU | `Github/Unlimited-OCR-main.zip` (reference for a future GPU edition) | opt-in |
| **CalamariEngine** | **Confidence voting** (5-model cross-fold ensemble) for degraded early-print / Byzantine typefaces; line-based; strong per-char confidence for faithful review-flagging | `Github/calamari-master.zip`, `calamari-ocr` (PyPI — **TensorFlow**, **GPLv3**) | v1.1, subprocess-isolated |

All engines implement `IOCREngine.extract(img, context) -> List[OCRBlock]`. Editions differ
only in **which engines the composition root wires up**.

### Licensing & runtime isolation

- **Permissive — safe to import into the core:** Tesseract, Kraken, PaddleOCR, EasyOCR (all
  Apache-2.0).
- **Calamari is GPLv3.** Importing `calamari_ocr` into the distributed app would impose GPLv3 on
  the whole product. Keep it **out of the core**: invoke its **CLI in a subprocess**, or ship it
  as an optional, separately-licensed plugin. This also isolates its **TensorFlow** dependency
  from the PyTorch stack (Kraken/VLM), so the base install stays lean and TF is pulled only when a
  user opts into Calamari.
- Net: **Kraken is the default recognizer** (permissive, PyTorch, also segments, has Greek
  models); **Calamari is the opt-in accuracy booster** whose voting shines on the hardest printed
  pages (Würzburg/OCRopy lineage, purpose-built for early-modern print).

---

## 5. Greek-aware post-correction (the differentiator)

Faithful mode = **every step below suggests or validates; none rewrites silently.**

- **NFC normalization** — Unicode hygiene only (`unicodedata.normalize('NFC', …)`), never an
  orthographic change. Handle precomposed ↔ decomposed polytonic.
- **Diacritic validation** — flag impossible breathing/accent combinations; resolve the
  context-dependent ϊ/ϋ (diaeresis vs diphthong) as a *suggestion*.
- **Ligature / abbreviation expansion** — a mapping table (ϗ→καί, ligatured ου/καί,
  nomina sacra) applied as a **reversible, opt-in layer**; default preserves the original.
- **Per-variety lexicons** — pluggable dictionaries used to **highlight** suspect tokens:
  - Modern: Hunspell `el_GR`
  - Ancient: CLTK / Morpheus lemma lists
  - **Byzantine**: custom lexicon
  - **Pontian**: custom lexicon — critical, because a generic corrector would "fix" real
    Pontian words into standard Greek. Correction is dialect-scoped and suggest-only.
- **LM rescoring** — optional, conservative; never overwrites a low-confidence-but-faithful
  reading.

---

## 6. Export

| Format | Library | Notes |
|--------|---------|-------|
| **Searchable PDF** | PyMuPDF / ocrmypdf-style overlay | Invisible text layer **over the original page image** (faithful + archival). NB: the prototype `ocr.py` draws text on a blank canvas — must overlay the image. |
| **DOCX** | python-docx | Use a Byzantine-capable font — **New Athena Unicode / Gentium Plus**, and **Athena Ruby** for Byzantine characters. (Prototype uses Palatino Linotype — insufficient glyph coverage.) |
| **Markdown** | reconstructed from layout | Headings/columns from region classification. |
| **TXT** | plain | NFC-normalized. |
| **ALTO-XML / PAGE-XML** | lxml | Positional, faithful, round-trippable; scholarly archival standard and v2 training input. |

---

## 7. Clean-architecture layout (complete `app_core`)

Dependencies point inward: `interfaces (UI/API) → infrastructure → application → domain`.

- **domain** — immutable models: `OCRBlock`, `OCRLine`, `OCRParagraph`, `DocumentStructure`,
  `TenantContext`. Frozen dataclasses.
- **interfaces (ports)** — `IImageProcessor`, `IOCREngine`, `IOCRStrategy`, `ILayoutAnalyzer`,
  `IExporter`, `ILexicon`, `IModelExporter`.
- **engines / services (adapters)** — concrete Tesseract/Kraken/Paddle/VLM engines, the
  router, reconciler, post-corrector, exporters.
- **composition roots** — one per edition, wiring ports to adapters:
  - **Desktop** (primary): offline, CPU, Streamlit/Tauri, bundles Tesseract + Kraken; VLM off.
  - **Server Standalone**: FastAPI + RQ + static UI, self-hosted.
  - **Cloud**: FastAPI + Celery + Redis, multi-tenant (`TenantContext`), GPU workers, VLM on.

---

## 8. Build order

1. **Complete `omniocr_core` for printed Greek (CPU)** — real `TesseractEngine` +
   `KrakenEngine`, PyMuPDF streaming ingest, script router, faithful post-correction
   (NFC + diacritic validation + suggest-only lexicon). Ships a genuinely useful tool.
2. **Layout + faithful exports** — critical-edition region classification, searchable-PDF
   over original, ALTO/PAGE-XML, correction UI, polytonic keyboard.
3. **Ligature/abbreviation layer + Pontian/Byzantine lexicons** — the variety coverage.
4. **VLMEngine (cloud API) + grounding guard** — opt-in, for messy layouts only.
5. **(v2) Training loop** — Kraken/Calamari fine-tune on specific Byzantine printed
   typefaces via MLflow, to push accuracy where pretrained models fall short.

---

## 9. Conventions

- **Separation of concerns**: no OpenCV/Tesseract/DB calls inside UI or API controllers;
  map everything into `domain` dataclasses; inject engines via `interfaces` ports.
- **Async / queues**: CPU-bound OCR runs off the request loop via `asyncio.to_thread`,
  `ThreadPoolExecutor`, or the edition's task queue (Celery/RQ).
- **Encoding**: normalize all final text with `unicodedata.normalize('NFC', …)`.
- **Faithfulness**: no stage silently alters recognized text; corrections are suggestions.
- **Typing**: strict; avoid `Any`/`cast` suppression; use explicit type guards.

---

## 10. Sources

- Kraken/Transkribus Byzantine HTR — https://ride.i-d-e.de/issues/issue-15/transkribus/
- Transkribus vs eScriptorium for Byzantine paleography — https://blog.stoa.org/archives/4308
- HTR for Greek Historical Documents (MDPI) — https://mdpi.com/2313-433X/7/12/260/htm
- Kraken+Ciaconna vs Tesseract CER (arXiv 2110.06817) — https://arxiv.org/pdf/2110.06817
- Ancient Greek OCR / Ciaconna — https://ancientgreekocr.org/
- VLM grounding failures in Ancient Greek OCR (arXiv 2605.27750) — https://arxiv.org/pdf/2605.27750
- DeepSeek-OCR 2 fine-tuning — https://dev.to/czmilo/deepseek-ocr-2-complete-guide-to-running-fine-tuning-in-2026-3odb
