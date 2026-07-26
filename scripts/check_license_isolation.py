from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1] / "packages" / "omniocr" / "src"
FORBIDDEN = re.compile(r"(?:import\s+calamari_ocr|from\s+calamari_ocr\b)")


def main() -> int:
    violations = [
        path for path in ROOT.rglob("*.py") if FORBIDDEN.search(path.read_text(encoding="utf-8"))
    ]
    if violations:
        for path in violations:
            print(f"Forbidden Calamari import in shared core: {path}", file=sys.stderr)
        return 1
    print("License isolation check passed: shared core has no Calamari imports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
