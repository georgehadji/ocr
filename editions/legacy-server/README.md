# Legacy Server Standalone Edition (migrated)

This directory contains the original Server Standalone Edition codebase,
preserved for reference. The active Server Edition lives in
`editions/server/` with a FastAPI + RQ composition root.

**Migration status:** The shared core (`packages/omniocr/`) now provides all
business logic. Edition-specific composition roots live in `editions/`.
