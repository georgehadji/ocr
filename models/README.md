# Pinned OCR models

Store only explicitly licensed model artifacts here. Record each artifact in a
reviewed manifest with its engine, model name, source/license, and SHA-256 hash.

The shared core exposes `sha256_file()` and `verify_model_hash()` for adapters
and CI to validate artifacts before recognition. Do not commit downloaded models
without a licensing decision.

## Missing: a Greek Kraken model (blocks a Phase 1 acceptance criterion)

This directory currently holds **no `.mlmodel` artifact**, which leaves a real
gap in test coverage rather than a merely cosmetic one:

- ARCHITECTURE.md §2 designates **Kraken as the accuracy driver** for polytonic,
  Ancient, and Byzantine printed Greek — the varieties Tesseract handles worst,
  and the ones this product exists to serve.
- BUILD_PLAN §10 Phase 1 requires *"Kraken beats Tesseract CER on the polytonic
  fixture."*
- `tests/test_engine_accuracy.py::test_kraken_beats_tesseract_on_hard_scripts`
  implements exactly that comparison, but **skips** while this directory is empty.

Net effect: Tesseract accuracy is measured and gated; **Kraken accuracy is not
tested at all.** Dropping a licensed Greek model here (e.g. a Ciaconna-family
`.mlmodel` for ancient/polytonic print) un-skips the comparison and closes the
criterion. Record its source, license, and SHA-256 in the manifest when you do.
