# Pinned OCR models

Store only explicitly licensed model artifacts here. Record each artifact in a
reviewed manifest with its engine, model name, source/license, and SHA-256 hash.

The shared core exposes `sha256_file()` and `verify_model_hash()` for adapters
and CI to validate artifacts before recognition. Do not commit downloaded models
without a licensing decision.
