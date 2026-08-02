"""Corpus repository — structured access to corpus pages.

Repository over ``tests/corpus/`` and real-scan corpus directories.
Supports filtering by split and script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from omniocr.domain.corpus import CorpusPage, SplitName
from omniocr.domain.models import Script
from omniocr.ports.interfaces import ICorpusRepository


class FileCorpusRepository(ICorpusRepository):
    """Corpus repository backed by a directory of page images and ground-truth files.

    Expected directory layout::

        corpus/
        ├── train/
        │   ├── ancient-1.png
        │   ├── ancient-1.txt
        │   ├── polytonic-1.png
        │   └── polytonic-1.txt
        ├── dev/
        │   └── ...
        └── test/
            └── ...
    """

    def __init__(self, corpus_root: str | Path) -> None:
        self._root = Path(corpus_root)

    def pages(self, split: SplitName, script: Script | None = None) -> Sequence[CorpusPage]:
        """Return corpus pages for the given split, optionally filtered by script.

        Pages are discovered from the filesystem by matching ``*.png`` /
        ``*.jpg`` / ``*.tiff`` files with corresponding ``*.txt`` ground-truth
        files. The page_id is derived from the filename stem.
        """
        split_dir = self._root / split.value
        if not split_dir.is_dir():
            return []

        result: list[CorpusPage] = []
        for image_path in split_dir.iterdir():
            if not image_path.is_file():
                continue
            if image_path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
                continue

            gt_path = image_path.with_suffix(".txt")
            if not gt_path.is_file():
                continue

            # Infer script from filename prefix
            page_id = image_path.stem
            inferred_script = _infer_script(page_id)
            if script is not None and inferred_script != script:
                continue

            # Infer provenance from directory structure
            provenance = f"corpus:{split_dir.name}/{page_id}"

            result.append(
                CorpusPage(
                    page_id=page_id,
                    image_path=image_path,
                    ground_truth_path=gt_path,
                    script=inferred_script,
                    split=split,
                    provenance=provenance,
                    is_synthetic=False,
                )
            )

        return result


def _infer_script(page_id: str) -> Script:
    """Infer the script from a page ID using known prefixes."""
    page_lower = page_id.lower()
    for prefix, script in [
        ("ancient", Script.ANCIENT),
        ("byzantine", Script.BYZANTINE),
        ("polytonic", Script.POLYTONIC),
        ("modern", Script.MODERN),
        ("pontian", Script.PONTIAN),
    ]:
        if page_lower.startswith(prefix):
            return script
    return Script.UNKNOWN


__all__ = ["FileCorpusRepository"]
