"""Deterministic corpus partitioning via stable hash-keyed splitting.

Hash-keyed rather than random: adding pages later must not reshuffle existing
ones, or every historical baseline becomes incomparable. Same ``page_id``
always lands in the same split.
"""

from __future__ import annotations

import hashlib

from omniocr.domain.corpus import DEFAULT_RATIOS, SplitName, SplitRatios


def assign_split(
    page_id: str,
    ratios: SplitRatios = DEFAULT_RATIOS,
) -> SplitName:
    """Assign a page to a split deterministically based on its ID.

    Uses SHA-256 hashing of the page_id to produce a stable, uniformly
    distributed value. Adding pages later does not reshuffle existing
    assignments.

    Args:
        page_id: Unique page identifier.
        ratios: Split proportions (default: 80/10/10).

    Returns:
        The assigned ``SplitName`` for this page.
    """
    digest = hashlib.sha256(page_id.encode("utf-8")).hexdigest()
    # Use the first 8 hex chars as a uniform integer [0, 2^32)
    hash_int = int(digest[:8], 16)
    total = 2**32
    threshold_dev = ratios.dev * total
    threshold_test = (ratios.dev + ratios.test) * total

    if hash_int < threshold_dev:
        return SplitName.DEV
    if hash_int < threshold_test:
        return SplitName.TEST
    return SplitName.TRAIN


def split_pages(
    page_ids: list[str],
    ratios: SplitRatios = DEFAULT_RATIOS,
) -> dict[SplitName, list[str]]:
    """Partition a list of page IDs into train/dev/test splits.

    Args:
        page_ids: All page IDs to partition.
        ratios: Split proportions.

    Returns:
        A dict mapping each ``SplitName`` to its list of page IDs.
    """
    result: dict[SplitName, list[str]] = {
        SplitName.TRAIN: [],
        SplitName.DEV: [],
        SplitName.TEST: [],
    }
    for page_id in page_ids:
        split = assign_split(page_id, ratios)
        result[split].append(page_id)
    return result


__all__ = ["assign_split", "split_pages"]
