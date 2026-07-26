"""Import bridge for the OmniOCR source package.

This keeps the repository usable before installation while the real
implementation lives under packages/omniocr/src/omniocr.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)  # type: ignore[name-defined]
_source_root = Path(__file__).resolve().parent.parent / "packages" / "omniocr" / "src" / "omniocr"
if _source_root.exists():
    __path__.append(str(_source_root))  # type: ignore[attr-defined]

