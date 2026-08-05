# Contributing to OmniOCR

## Setup

```bash
pip install -e ".[dev,docx]"
```

Tesseract must be on `PATH` with `ell` and `grc` language data for the OCR
tests to run rather than skip. See `omniocr doctor` to check what's missing.

## Before opening a PR

Run the same gates CI runs:

```bash
python -m pytest --cov=packages/omniocr/src/omniocr --cov-report=term-missing --cov-fail-under=80
ruff check packages tests
ruff format --check packages tests
mypy --strict --ignore-missing-imports --python-version 3.11 packages/omniocr/src
python scripts/check_license_isolation.py
python scripts/check_layering.py
```

## Rules specific to this codebase

These are enforced by review, and some mechanically by the gates above — see
`CLAUDE.md` for the full list. The ones most likely to catch out a new PR:

1. **Faithfulness.** No pipeline stage may silently alter recognized text.
   Post-correction emits suggestions; it never rewrites source text. This is
   property-tested — do not weaken that test to make a change pass.
2. **Dependency direction.** `domain` imports nothing outward; `application`
   never imports `infrastructure`; editions never construct an adapter
   directly — they compose via `omniocr.composition`. `scripts/check_layering.py`
   gates this in CI.
3. **License isolation.** Calamari (GPLv3) is subprocess-isolated and must
   never be imported from core. `scripts/check_license_isolation.py` gates
   this in CI.
4. **No new `type: ignore`.** If mypy strict complains, fix the actual typing
   gap (a type guard, a correct annotation, a scoped `[[tool.mypy.overrides]]`
   entry for a genuinely-untyped third-party dependency) rather than
   suppressing the check.

## Commit messages

Conventional Commits (`feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`).
Explain *why*, not just *what* — the diff already shows what changed.
