"""Enforce the dependency rule mechanically, so architecture can't silently drift.

Three rules, each one a violation this repo actually shipped at some point:

1. ``domain`` imports nothing from outer layers — the whole point of a domain.
2. ``application`` and ``ports`` never import ``infrastructure``. Dependencies
   point inward; an application layer that reaches for a concrete adapter is no
   longer testable without it.
3. ``editions`` never import ``infrastructure`` adapters directly. They compose
   via ``omniocr.composition``. This one is the reason the check exists: all
   three editions once hand-wired their own pipelines, which silently dropped
   the resilience wrappers and the VLM grounding guard, and left the desktop UI
   with no recognition engine at all.

Documentation asserts these rules; only a gate keeps them true.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "packages" / "omniocr" / "src" / "omniocr"
EDITIONS = ROOT / "editions"

# Adapters editions must not construct themselves. `config`, `logging`, `jobs`,
# `security`, `exporters` and `review` are deliberately allowed: they are
# edition-level concerns (settings, observability, persistence choice, upload
# validation, output format) rather than recognition wiring.
FORBIDDEN_IN_EDITIONS = re.compile(
    r"from\s+omniocr\.infrastructure\.(kraken|tesseract|vlm|calamari|preprocess|ingest|resilience)\b"
    r"|from\s+omniocr\.application\.(router|reconcile)\b"
)


def _imports(path: Path, pattern: re.Pattern[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if pattern.search(line)]


def _check(label: str, roots: list[Path], pattern: re.Pattern[str], hint: str) -> list[str]:
    problems: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for line in _imports(path, pattern):
                rel = path.relative_to(ROOT)
                problems.append(f"{label}: {rel}: {line}\n    → {hint}")
    return problems


def main() -> int:
    problems: list[str] = []

    problems += _check(
        "domain purity",
        [CORE / "domain"],
        re.compile(r"from\s+omniocr\.(application|infrastructure|ports|composition|interfaces)\b"),
        "domain must not depend on any outer layer",
    )
    problems += _check(
        "dependency rule",
        [CORE / "application", CORE / "ports"],
        re.compile(r"from\s+omniocr\.infrastructure\b"),
        "dependencies point inward — inject through a port instead",
    )
    problems += _check(
        "edition composition",
        [EDITIONS],
        FORBIDDEN_IN_EDITIONS,
        "compose via omniocr.composition, do not wire adapters in an edition",
    )

    if problems:
        print("Layering check FAILED:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("Layering check passed: domain pure, dependencies inward, editions composed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
