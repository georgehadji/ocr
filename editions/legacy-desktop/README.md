# Legacy Desktop Edition (migrated)

This directory contains the original Desktop Trainer Edition codebase,
preserved for reference. The active Desktop Edition review UI lives in
`editions/desktop/review_ui.py`.

**Migration status:** The shared core (`packages/omniocr/`) now provides all
business logic. Edition-specific composition roots live in `editions/`.
