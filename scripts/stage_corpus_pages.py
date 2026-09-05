"""Stage pages from a source PDF as pending corpus fixtures (ENHANCEMENT_PLAN A1).

The first three scan fixtures were staged by hand: render a page, name it,
hand-write a PROVENANCE entry, remember which PDF index it came from. That
does not scale to the 115 pages A1 needs, and the manual step most likely to
go wrong is the one that matters most — recording *exactly* which page of
which document a transcription belongs to.

This renders the pages, writes the PROVENANCE entries with the source
coordinates already filled in, and leaves the transcription itself to a human.
It deliberately does not write a `.txt`: `list_fixture_ids()` globs `*.txt`, so
a staged page is inert until someone transcribes it, and no gate can
accidentally measure against machine output. `tests/corpus/README.md` — "ground
truth must come from a human reading the page, never seeded from OCR output" —
is enforced here by simply not having the capability.

Usage:

    python scripts/stage_corpus_pages.py \
        --pdf "PDFs for OCR/Some Book.pdf" \
        --pages 12,40,61 \
        --prefix polytonic-scan \
        --script polytonic \
        --typeface "20c polytonic serif" \
        --source-title "Τα Βυζαντινά Μνημεία της Θεσσαλονίκης"

Page numbers are 0-based PDF indices, matching how the existing fixtures
record them. Run with --dry-run first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORPUS = Path("tests/corpus")
PROVENANCE = CORPUS / "PROVENANCE.json"

# Matches infrastructure/ingest.py RENDER_DPI. Kept as an explicit default
# rather than imported, because the corpus is allowed to outlive a change to
# the pipeline's render setting — a fixture's DPI is a property of the fixture.
DEFAULT_DPI = 300
_PDF_POINTS_PER_INCH = 72

LICENCE_PLACEHOLDER = "FILL IN — see tests/corpus/TRANSCRIBE.md"


def render_page(pdf_path: Path, page_index: int, dpi: int) -> tuple[bytes, int, int]:
    """Render one PDF page to PNG bytes at the given DPI."""
    import fitz

    with fitz.open(pdf_path) as document:
        if not 0 <= page_index < document.page_count:
            raise SystemExit(
                f"page index {page_index} out of range: {pdf_path.name} has "
                f"{document.page_count} pages (0-{document.page_count - 1})"
            )
        zoom = dpi / _PDF_POINTS_PER_INCH
        pixmap = document[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return bytes(pixmap.tobytes("png")), pixmap.width, pixmap.height


def next_free_id(prefix: str, existing: set[str]) -> str:
    """First unused `<prefix>-N`, so a second run does not overwrite the first."""
    index = 1
    while f"{prefix}-{index}" in existing:
        index += 1
    return f"{prefix}-{index}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument(
        "--pages", required=True, help="comma-separated 0-based PDF page indices, e.g. 12,40,61"
    )
    parser.add_argument("--prefix", required=True, help="fixture id prefix, e.g. polytonic-scan")
    parser.add_argument(
        "--script",
        required=True,
        choices=["modern", "polytonic", "ancient", "byzantine", "pontian"],
    )
    parser.add_argument("--typeface", required=True, help='e.g. "19c German serif"')
    parser.add_argument("--source-title", required=True)
    parser.add_argument("--licence", default=LICENCE_PLACEHOLDER)
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"no such PDF: {args.pdf}")

    try:
        page_indices = [int(part) for part in args.pages.split(",") if part.strip()]
    except ValueError:
        raise SystemExit(f"--pages must be comma-separated integers, got: {args.pages}") from None
    if not page_indices:
        raise SystemExit("--pages listed no pages")

    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    fixtures = provenance["fixtures"]
    taken = set(fixtures)

    staged: list[tuple[str, int]] = []
    for page_index in page_indices:
        fixture_id = next_free_id(args.prefix, taken)
        taken.add(fixture_id)
        content, width, height = render_page(args.pdf, page_index, args.dpi)

        fixtures[fixture_id] = {
            "tier": "scan",
            "script": args.script,
            "source": (
                f"{args.source_title}, PDF page index {page_index}, {args.dpi} DPI, "
                f"{width}x{height}px"
            ),
            "licence": args.licence,
            "transcribed_by": "PENDING — no ground truth yet",
            "typeface": args.typeface,
            "note": "Staged by scripts/stage_corpus_pages.py. Inert until a .txt exists.",
        }
        staged.append((fixture_id, page_index))

        if not args.dry_run:
            (CORPUS / f"{fixture_id}.png").write_bytes(content)

    if not args.dry_run:
        PROVENANCE.write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    prefix = "[dry run] would stage" if args.dry_run else "staged"
    for fixture_id, page_index in staged:
        print(f"{prefix} {fixture_id}.png  <- {args.pdf.name} page index {page_index}")
    print(f"\n{len(staged)} page(s). Next: transcribe each to tests/corpus/<id>.txt")
    if args.licence == LICENCE_PLACEHOLDER and not args.dry_run:
        print("WARNING: licence left as a placeholder — the provenance gate will fail until set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
