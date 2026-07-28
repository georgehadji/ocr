# Pinned OCR models

Store only explicitly licensed model artifacts here. Record each artifact in a
reviewed manifest with its engine, model name, source/license, and SHA-256 hash.

## Models available

| Model | Engine | Source | Licence | Size |
|---|---|---|---|---|
| `greek-english_porson_sophoclesplaysa05campgoog.mlmodel` | Kraken | AjaxMultiCommentary/OCR-kraken-models | CC-BY-4.0 | 17 MB |
| `greek-german_serifs_sophokle1v3soph.mlmodel` | Kraken | AjaxMultiCommentary/OCR-kraken-models | CC-BY-4.0 | 18 MB |
| `greek-german_serifs_bsb10234118.mlmodel` | Kraken | AjaxMultiCommentary/OCR-kraken-models | CC-BY-4.0 | 18 MB |

These are the Ciaconna-family models described in arXiv 2110.06817, trained on
the Pogretra dataset for polytonic/ancient Greek print. All three are validated
by their SHA-256 hashes in `manifest.json`.

The shared core exposes `sha256_file()` and `verify_model_hash()` for adapters
and CI to validate artifacts before recognition.

## Adding a model

1. Place the artifact file here.
2. Compute its SHA-256 digest: `python -c "from omniocr.infrastructure.models import sha256_file; print(sha256_file('models/your-model.mlmodel'))"`
3. Add an entry to `manifest.json` with engine, name, source, licence, sha256.
