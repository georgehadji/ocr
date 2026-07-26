"""pytest configuration and shared fixtures for OmniOCR tests."""

from __future__ import annotations

import sys
from pathlib import Path

from omniocr.testing.fixtures import set_corpus_path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packages" / "omniocr" / "src"

for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Point fixture helpers at the repository's corpus directory.
set_corpus_path(ROOT / "tests" / "corpus")
