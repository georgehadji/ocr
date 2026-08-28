# Polytonik — Commercial Plan and Licence Remediation

**Status:** proposed, not started
**Date:** 2026-08-28
**Owner:** Polytonik (pre-incorporation)
**Companion to:** [ENHANCEMENT_PLAN.md](ENHANCEMENT_PLAN.md), which assumed an MIT
open-source project. This document supersedes its licensing assumptions and adds the
commercial workstreams. Where the two disagree, this one wins.

> Licence analysis below is engineering due diligence based on published package
> metadata, verified 2026-08-28. It is not legal advice. Items marked **[COUNSEL]**
> need a lawyer before you ship or sign anything.

---

## 1. The headline

**One hard blocker, one easy removal, one free window that is currently open.**

1. **PyMuPDF is AGPL-3.0.** It is your only PDF ingest path. Shipping a proprietary
   product on it — desktop *or* SaaS — obliges you to release your entire source, unless
   you buy a commercial licence from Artifex. Must be resolved before any customer,
   pilot, or investor demo.
2. **Calamari is GPLv3.** It is optional, off by default, and superseded by planned work.
   Delete it. Cost of removal: zero product capability.
3. **Your repo is private, unforked, unstarred, and has never been published.** The MIT
   `LICENSE` file therefore grants nothing to anyone yet. You can change it to
   proprietary today at zero cost. That window closes the first time a contractor,
   collaborator, or public push sees it. **Do this first — it is a ten-minute task.**

Everything else in the dependency tree is commercially clean.

---

## 2. Licence audit

Verified against PyPI package metadata on 2026-08-28.

### 2.1 Blockers

| Component | Licence | Where it bites | Verdict |
|---|---|---|---|
| **PyMuPDF** `>=1.24` | `Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License` | `pdf` extra. Used in `ingest.py` (300 DPI render), `pipeline.py` (page count), `exporters.py` (searchable PDF text layer), `review_ui.py`, `prototype/ocr.py`. **The core ingest path.** | **BLOCKER.** AGPL §13 triggers on *network interaction*, not just distribution — so the Cloud and Server editions are caught even without shipping a binary, and the Desktop edition is caught by distribution. Replace or buy. |
| **calamari-ocr** `>=2.0` | `GPL version 3` | `calamari` extra, optional voting booster, `enable_calamari` defaults to `False`. | **REMOVE.** `CLAUDE.md`'s subprocess-isolation note is the conventional mitigation, but the arm's-length-process argument is contested and not a position a pre-seed company should defend. W3 (alignment merge) and W5 (bake-off) deliver the accuracy Calamari was there for. |

### 2.2 Clean — commercial use permitted

| Component | Licence | Obligation |
|---|---|---|
| Kraken `>=6.0` | Apache-2.0 | Notice + attribution. |
| Tesseract / pytesseract | Apache-2.0 | Notice + attribution. |
| Kraken models (3 × Ciaconna family, `models/manifest.json`) | CC-BY-4.0 | **Attribution required and currently missing.** Commercial use is permitted. Credit AjaxMultiCommentary and arXiv 2110.06817 in `NOTICE` and in the product's About screen. |
| opencv-python-headless | Apache-2.0 | Notice. |
| Pillow | MIT-CMU | Notice. |
| python-docx | MIT | Notice. |
| FastAPI, python-multipart | MIT | Notice. |
| Streamlit | Apache-2.0 | Notice. |
| Celery, RQ | BSD-3-Clause | Notice. |
| redis-py | MIT | Notice. |
| MLflow | Apache-2.0 | Notice. Internal tooling — not shipped. |
| pytest, ruff, mypy, hypothesis, bandit, pip-audit | MIT / BSD / Apache | Dev-only, not distributed. |

### 2.3 Replacement stack for PyMuPDF

PyMuPDF does two separable jobs. One replacement does not cover both — plan for two.

| Job | Current | Replacement | Licence |
|---|---|---|---|
| Render PDF page → bitmap at 300/400 DPI; page count | `fitz.open` + `fitz.Matrix` | **pypdfium2** | BSD-3-Clause + Apache-2.0 |
| Searchable PDF: image + invisible text layer at word boxes | `fitz.Rect` + invisible-render-mode text insert | **reportlab** | BSD-3-Clause |

pypdfium2 is a binding to PDFium — Chrome's PDF engine — and is render-only by design.
It cannot write a text layer. reportlab builds the output PDF from scratch: page image
as background, `textobject.setTextRenderMode(3)` for invisible text positioned at the
Tesseract word boxes you already extract. That is exactly what `SearchablePdfExporter`
does today, in a different API.

**Alternative: buy the Artifex commercial licence.** Keeps the code unchanged, costs a
per-year fee (quote required — Artifex does not publish pricing), and creates a
per-seat or per-revenue dependency on a third party for your core ingest path.
Recommended only if W-1 measurement shows pypdfium2 render fidelity is materially worse
on your target material — which is testable in half a day against the W1 corpus, and
should be, before committing either way.

**Recommendation: replace.** Zero recurring cost, no vendor dependency, both
replacements are permissive, and pypdfium2 is actively maintained and production-stable.

---

## 3. W-1 — Licence remediation (blocking, do first)

Inserted **before W0** in the enhancement plan. Nothing else should ship until this closes.

**Why.** A commercial product cannot be built on an AGPL core. Every day of further
development on `fitz` increases the cost of the swap.

**Paradigm and pattern.** No new paradigm — this is adapter replacement behind an
existing port. `IPageSource` and `IExporter` already isolate PyMuPDF to the
infrastructure layer, which is precisely why this is a four-day job and not a rewrite.
The clean architecture is now paying for itself; the only leak is `pipeline.py:371`,
which imports `fitz` directly from the **application** layer for a fast page count.
That is a layer violation (CLAUDE.md rule 4) and gets fixed as part of this work by
moving page-count behind `IPageSource`.

**Surface.**

```
pyproject.toml                    change  pdf = ["pypdfium2>=4.30", "reportlab>=4.2"]
                                  remove  calamari extra
infrastructure/ingest.py          rewrite DocumentPageSource on pypdfium2
infrastructure/exporters.py       rewrite SearchablePdfExporter on reportlab
application/pipeline.py           change  remove the direct `import fitz`; page count
                                          moves behind IPageSource (fixes a layer leak)
interfaces/cli.py                 change  doctor probes pypdfium2, not fitz
infrastructure/calamari.py        delete
composition/desktop.py            change  drop Calamari wiring
editions/desktop/review_ui.py     change  page render via the port, not fitz
prototype/ocr.py                  leave   never distributed; keep or delete, no exposure
LICENSE                           replace MIT → proprietary, © Polytonik
NOTICE                            new     third-party attributions incl. CC-BY-4.0 models
pyproject.toml                    change  license = "LicenseRef-Proprietary"
.github/workflows/               add     pip-licenses gate — CI fails on any
                                          GPL/AGPL/SSPL/CC-BY-SA in the dependency tree
tests/test_licences.py            new     assert no copyleft in the resolved tree
```

**The CI gate is the durable part.** Everything above is a one-time fix; the gate is
what stops a future `pip install` from silently reintroducing the problem. Fail the
build on any dependency whose licence is not on an allowlist.

**Security.** Neutral to positive. pypdfium2 wraps PDFium, which is Chrome's
battle-tested, fuzzed, sandboxed-in-production PDF parser — a stronger position on
malicious-PDF handling than MuPDF, on a threat surface you are deliberately exposing to
untrusted library scans. Keep `validate_upload` and add W0's page and pixel budgets.

**Tests.** Existing `tests/test_ingest.py` and `tests/test_exporters.py` must pass
unchanged against the new adapters — that is the point of having ports. Add: rendered
bitmap dimensions match at 300 DPI; searchable-PDF text layer is extractable and
positioned within tolerance of the source word boxes; `test_licences.py` fails on an
injected GPL dependency.

**DoD.** No `import fitz` anywhere in `packages/` or `editions/`. No `calamari` in
`pyproject.toml`. `LICENSE` is proprietary. `NOTICE` complete and attributes the
CC-BY-4.0 models. CI licence gate green and proven to fail on a planted violation.
Rendered-page CER on the W1 corpus unchanged from the PyMuPDF baseline — this is the
acceptance test that the swap cost nothing.

**Effort.** 4 days. The LICENSE swap inside it is a ten-minute task that should happen
on day one regardless of when the rest lands.

---

## 4. IP hygiene

| Item | Action | Urgency |
|---|---|---|
| **Licence swap** | `LICENSE` → proprietary, © Polytonik. Repo is private with zero forks, so no grant has been made and no one can rely on the MIT text. Free today. | **Today** |
| **Copyright holder** | Currently no company exists. Assign to yourself now, then execute an IP assignment from you to Polytonik at incorporation. Retroactive assignment of pre-incorporation IP is routine but must actually be signed. **[COUNSEL]** | At incorporation |
| **Contributor assignment** | Anyone who has touched or will touch the code — contractor, collaborator, co-founder — signs an assignment or CLA *before* their first commit. Retrofitting this is the most common way a seed round stalls. **[COUNSEL]** | Before next contributor |
| **AI-generated code** | Substantial portions of this codebase were AI-assisted. Copyright in purely machine-generated output is unsettled in both the US and EU, which can affect what you can claim to own and what a diligence process will ask about. Keep the commit history — it evidences human direction and iteration. Raise it with counsel rather than discovering it in diligence. **[COUNSEL]** | Before raising |
| **Trademark "Polytonik"** | Search EUIPO and USPTO for conflicts in classes 9 (software) and 42 (SaaS). "Polytonic" is descriptive of the subject matter, so a coined mark like *Polytonik* is more defensible — but check that it is not already taken. **[COUNSEL]** | Before public launch |
| **Domains and handles** | `polytonik.com`, `.eu`, `.gr`. Register before announcing anything. | Now |
| **PyPI name** | `omniocr` is the current package name and does not match the brand. Reserve `polytonik` on PyPI even if the product ships closed — it prevents squatting and typo-attacks against your customers. Decide whether the public name is `polytonik` or stays `omniocr` internally. | Before first release |
| **Product name** | "OmniOCR" describes an OCR suite generically; "Polytonik" says exactly what it is for. Renaming later costs documentation, URLs, and mindshare. Decide now. | Now |
| **Secrets audit** | Confirm no API keys ever entered git history before the repo is shared with anyone. `vlm_api_key` is correctly `repr=False`, but check the history, `.streamlit/` configs, and the three committed `.db` files. | Before sharing |
| **Committed databases** | `omniocr_jobs.db`, `omniocr_cloud_jobs.db`, `omniocr_cloud_jobs_test.db` are tracked in the repo root. Verify they contain no real document content or customer data, then gitignore them. | Now |

---

## 5. Customer data, and why local-first is the product

Your buyers are classicists, university libraries, and scholarly publishers. Their
material is frequently **unpublished, embargoed, or in-copyright** — a doctoral edition
in progress, a manuscript facsimile under an archive agreement, a publisher's
pre-press text. For that audience, *where the bytes go* is not a technical footnote.
It is the purchase decision.

This makes the CPU-only, local-first architecture a **commercial asset**, not just an
engineering constraint. Say so on the front page. "Your texts never leave your machine"
is a claim Mistral OCR, Deepseek OCR, and GLM OCR structurally cannot make.

Which turns the VLM (W6) into a contractual question, not only a design one:

- **Off by default, and stay that way.** Already the case (`OMNIOCR_ENABLE_VLM=false`).
- **Explicit per-run, per-document opt-in** with a plain-language warning naming the
  endpoint and the operator.
- **Egress manifest** (already in W6): page numbers, line ids, crop dimensions, byte
  counts, endpoint, model. A librarian must be able to prove to their archive exactly
  what left the building.
- **Zero-retention endpoint or none at all.** If you offer a hosted VLM tier, contract
  for zero training-data retention with the provider and pass that guarantee through.
- **Site-licence tier with VLM hard-disabled at build time**, for institutions whose
  policy forbids third-party egress. This is a feature you can charge for.

**GDPR. [COUNSEL]** EU universities are the core market and will ask before they buy.
Desktop-only processing keeps you largely out of scope, which is itself a selling point.
The Cloud edition needs a lawful basis, a DPA offered to every customer, an EU hosting
option, and a documented retention policy. The three committed `.db` job stores are
exactly the kind of thing an auditor asks about — resolve them per §4.

---

## 6. Moat

The competitive question is direct: *Mistral OCR, Deepseek OCR, and GLM OCR are good and
getting better. Why does Polytonik exist?*

The hybrid-OCR literature answers most of it. Recent work — the arXiv hybrid OCR-LLM
framework (2510.10138), and the practitioner writing you sent — converges on findings
that happen to describe your architecture:

- **VLM-only is slower, not more accurate.** The arXiv paper measures a multimodal
  baseline at F1 0.999 / **33.9 s** against a hybrid pipeline at 0.997 / **0.6 s** — a
  54× latency penalty for no accuracy gain. At book scale that difference is the
  difference between a product and a demo.
- **Deterministic structure beats end-to-end generation** on repetitive, structured
  material. Their phrasing — sidestep hallucination through *method selection* rather
  than model improvement — is W3 and W6 stated as a research finding.
- **Use the VLM for structure, not characters.** The strongest recommendation in the
  practitioner piece: OCR is authoritative for text, the VLM analyses layout only.
  A VLM that never emits a character cannot hallucinate one. See §7 — this is a genuine
  addition to the plan.

None of that is a moat by itself; competitors can read the same papers. The defensible
assets are:

| Asset | Why it is hard to copy |
|---|---|
| **Faithfulness with an audit trail** | A publisher cannot accept a transcription that might have been silently improved. Every character traceable to an engine, a model hash, and a box on the page — with a review UI where a human signs off — is a *workflow* product, not a model feature. Generic OCR vendors are optimising for the opposite property. |
| **The ground-truth corpus (W1)** | Twenty-plus pages of hand-verified diplomatic transcription across six Greek varieties, with provenance. Expensive, slow, and unglamorous to build. It is simultaneously your test set, your marketing evidence, and your fine-tuning seed. **Keep it proprietary.** |
| **Per-typeface model bank (W5)** | Your own manifest shows a 7× CER spread across three models on the same page. Knowing *which* model for *which* edition family — and having measured it — is accumulated domain knowledge no general-purpose vendor will bother to acquire. |
| **Apparatus criticus (W9)** | Critical editions are a real, paying, underserved market. No general OCR product models the apparatus zone, because outside classics nobody has one. |
| **Pontian and Byzantine coverage** | Small markets, zero competition, high willingness to pay from the institutions that care. |
| **Greek-specific error model (W8)** | Iotacism, breathings, lunate sigma, ligature splits. A generic spellchecker actively damages this material by normalising legitimate historical forms. |

**Positioning, in one line:** *not the best OCR — the only OCR a classicist can cite.*

---

## 7. Plan changes

The enhancement plan stands. Five changes.

### 7.1 New: W-1, blocking

Licence remediation (§3). Before W0. 4 days.

### 7.2 W7 lexicon — sourcing is now materially constrained

The MIT-wheel argument in `ENHANCEMENT_PLAN.md` §W7 was already cautious; commercial
distribution makes it stricter. **CC-BY-SA share-alike is now the primary risk**, because
a wordlist derived from a CC-BY-SA corpus is plausibly a derivative work that must itself
be licensed share-alike — which is incompatible with a proprietary data asset. **[COUNSEL]**

Revised sourcing:

| Lexicon | Source | Commercial verdict |
|---|---|---|
| Modern monotonic | Hunspell `el_GR` under the **MPL** arm of its tri-licence | **Usable.** MPL copyleft is file-level; keep the derived list in its own file with the MPL header and it does not reach your code. |
| Ancient / polytonic | Public-domain lexica (LSJ 1940 and comparable out-of-copyright sources), processed by you | **Usable and preferable.** Also better attested than a scraped list. |
| Perseus / CLTK form lists | CC-BY-SA | **Do not bundle.** Offer as an optional user-fetched add-on via `build_lexicon.py`, downloaded on the user's machine under their acceptance of the upstream terms. Never in the wheel, never in the paid artifact. |
| Greek Wiktionary | CC-BY-SA | Same treatment. |
| Byzantine, Pontian | Curated in-repo | **Yours.** These are hand-built and small — and precisely the coverage no competitor has. Expand them deliberately; they are an asset, not filler. |
| Customer corrections | Accumulated via the review UI | **Highest-value source, and the one that compounds.** Requires explicit opt-in and a clear grant in the EULA. Never harvest silently. **[COUNSEL]** |

The last row is the strategic one. Every correction a scholar accepts is labelled
training data for the exact material you are worst at. Design the opt-in now, in the
EULA and the UI, rather than retrofitting consent to data you already hold.

### 7.3 W9 gains a VLM-layout option

From the practitioner article: use the VLM for **structure only, never characters**.

W9's apparatus detection currently plans a 5-of-5 geometric heuristic — line height,
vertical position, separator rule, siglum density, length variance — requiring 3 signals
to fire. Region segmentation is exactly what VLMs are good at and hand-tuned geometry is
bad at.

So add a second `IRegionClassifier` strategy that asks the VLM for **region boundaries
and reading order only**, with a response schema containing no text field. It cannot
hallucinate a character because it is never asked for one, which makes it the only VLM
use in the product with *zero* faithfulness risk. The geometric classifier stays as the
offline default; the VLM classifier is the opt-in accuracy tier.

Effort: +1.5 days on W9. Depends on W6's client work.

### 7.4 W1 corpus is reclassified as a company asset

Not test data. Handle accordingly: public-domain sources only, provenance recorded per
page, licence determination per page, held in a private repository, and **excluded from
anything you open-source or ship to a customer**. It is the seed of every accuracy claim
and every fine-tune you will ever make.

### 7.5 W6 egress becomes contractual

Already specified technically. Now also needs: the EULA clause, the DPA, the
zero-retention agreement with the VLM provider, and the build-time-disabled site-licence
variant. Engineering unchanged; paperwork added. **[COUNSEL]**

---

## 8. Packaging sketch

Not part of the engineering plan — recorded so the technical decisions above have a
commercial target to aim at. Three plausible tiers; **build one first.**

| Tier | Buyer | Shape | Notes |
|---|---|---|---|
| **Desktop** | Individual scholar, doctoral candidate | Perpetual licence + annual maintenance | Local-only, VLM off. Matches the locked "Desktop edition primary" scope. Lowest support burden, cleanest data story, no GDPR exposure. |
| **Site** | University library, department, publisher | Annual site licence, seat-banded | Server edition. VLM disabled at build time on request. This is where the money is, and where the apparatus-criticus and per-typeface work earns its price. |
| **Cloud** | Casual and evaluation use | Per-page | Highest operational and legal cost — DPA, hosting, retention policy, GDPR. **Defer.** It is a funnel, not a business, and it is the only tier that puts customer manuscripts on your infrastructure. |

**Recommendation: Desktop first, Site second, Cloud last or never.** Desktop is what the
architecture is already optimised for, it is the tier your faithfulness and local-only
story sells hardest, and it carries no data-processing liability at all.

---

## 9. Revised sequencing

```
NOW (today)          LICENSE → proprietary. Domains. Check the committed .db files.
                     10 minutes, zero cost, closes the free window.

W-1  4d  BLOCKING    PyMuPDF → pypdfium2 + reportlab. Drop Calamari.
                     NOTICE. CI licence gate.
                     ── nothing ships before this closes ──

Phase 1   8.5d       W0 security · W1 corpus (proprietary) · W2 eval
Phase 2   9d         W3 alignment · W4 tiers · W5 bake-off
Phase 3   6d         W7 lexicon (revised sourcing) · W8 confusion
Phase 4   8.5d       W6 arbiter (+ contractual) · W9 apparatus (+ VLM-layout)

Total ~36 days engineering.

Parallel, non-engineering, start now:
  · Lexicon licence review              [COUNSEL]
  · Trademark search                    [COUNSEL]
  · Incorporation + IP assignment       [COUNSEL]
  · EULA incl. correction-data opt-in   [COUNSEL]
  · Artifex quote (fallback for W-1)
```

---

## 10. Open questions for you

1. **Artifex quote or replace?** Recommendation is replace, but get the quote so the
   decision is priced. Half a day of pypdfium2 render-fidelity testing against the W1
   corpus settles it either way.
2. **Product name — OmniOCR or Polytonik?** Affects the package name, docs, domains, and
   the trademark filing. Cheapest to decide before W-1 touches `pyproject.toml`.
3. **Which tier first?** The recommendation is Desktop. It changes what W6 and the cloud
   edition are worth building at all.
4. **Is anyone else contributing?** If yes, assignment paperwork precedes their first
   commit, not follows it.
5. **Is `prototype/ocr.py` ever distributed?** If never, it can keep `fitz` and W-1
   shrinks slightly. If it ships to anyone, even a demo, it is in scope.
