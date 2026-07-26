# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python monorepo for OmniOCR. The installable core lives in
`packages/omniocr/src/omniocr`, organized by clean-architecture layers:
`domain` contains immutable models and results, `application` contains pipeline
and routing logic, `infrastructure` contains adapters, and `composition` wires
editions together. Root-level `omniocr/` and `ocr.py` are prototype/compatibility
entry points. Tests are in `tests/`; design and build documentation is in
`docs/`. `Cloud Edition/`, `Desktop Edition/`, and `Server Standalone Edition/`
contain edition-specific composition roots and UIs. Keep business logic in the
shared package, not in edition controllers.

## Build, Test, and Development Commands

Use Python 3.11 or newer. Install development dependencies with:

```text
python -m pip install -e ".[dev]"
```

Run the test suite with `python -m pytest`. Run `ruff check .` for linting and
`ruff format --check .` to verify formatting. Run `mypy packages/omniocr/src`
for type checking. Build the package with `python -m build` after installing
the `build` package. The pytest configuration in `pyproject.toml` discovers
tests from `tests/` and enables quiet output.

## Coding Style & Naming Conventions

Use four spaces, typed Python, and a maximum line length of 100 characters.
Prefer immutable frozen dataclasses for domain values and explicit types over
`Any`. Use `snake_case` for modules, functions, and variables; `PascalCase` for
classes; and descriptive test names beginning with `test_`. Keep OCR side
effects behind ports and adapters, and normalize final text to Unicode NFC.

## Testing Guidelines

Tests use `pytest`. Add focused unit tests beside the relevant behavior in
`tests/`, and preserve the faithfulness invariant: correction may create
suggestions but must not silently mutate recognized source text. Add fixtures
for new OCR engines or script varieties under `tests/` (or the planned
`tests/corpus/`) and run `python -m pytest` before submitting changes.

## Commit & Pull Request Guidelines

Use concise imperative commit subjects with a scope when useful, for example
`feat(core): add script routing` or `fix(ingest): stream PDF pages`. Pull
requests should explain the behavior change, list validation commands, link a
relevant issue or plan item when available, and include screenshots or sample
OCR output for UI/export changes. Call out model, dependency, licensing, or
configuration changes explicitly.

## Security & Configuration Tips

Never commit API keys, uploaded documents, generated OCR output, or model
artifacts without an explicit licensing decision. Read secrets from environment
variables, validate input paths and uploads, and keep optional Calamari/GPLv3
integration isolated from the core package as described in `docs/ARCHITECTURE.md`.
