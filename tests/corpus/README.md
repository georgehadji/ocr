# OCR regression corpus

This directory contains small, licensed fixture pages and their diplomatic
transcriptions for CER/WER regression tests. Each fixture must include:

- the source page image or PDF,
- a UTF-8 ground-truth text file,
- the script variety (`modern`, `polytonic`, `ancient`, `byzantine`, or `pontian`),
- provenance and licensing information.

Do not add uploaded documents or generated OCR output. Baselines should be
updated only alongside a reviewed model or preprocessing change.
