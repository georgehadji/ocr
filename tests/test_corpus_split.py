"""Tests for application/corpus_split.py — deterministic split partitioning."""

from __future__ import annotations

from omniocr.application.corpus_split import assign_split, split_pages
from omniocr.domain.corpus import SplitName, SplitRatios


class TestAssignSplit:
    def test_same_id_always_same_split(self) -> None:
        """Hash-keyed split must be deterministic."""
        result1 = assign_split("page-ancient-1")
        result2 = assign_split("page-ancient-1")
        assert result1 == result2

    def test_all_splits_get_pages(self) -> None:
        """Over many IDs, all three splits should appear."""
        ids = [f"page-{i}" for i in range(100)]
        splits = {assign_split(pid) for pid in ids}
        assert SplitName.TRAIN in splits
        assert SplitName.DEV in splits
        assert SplitName.TEST in splits

    def test_distribution_near_ratios(self) -> None:
        """Distribution should roughly follow 80/10/10."""
        ids = [f"page-{i}" for i in range(10000)]
        counts = {SplitName.TRAIN: 0, SplitName.DEV: 0, SplitName.TEST: 0}
        for pid in ids:
            counts[assign_split(pid)] += 1
        total = sum(counts.values())
        assert abs(counts[SplitName.TRAIN] / total - 0.80) < 0.02
        assert abs(counts[SplitName.DEV] / total - 0.10) < 0.015
        assert abs(counts[SplitName.TEST] / total - 0.10) < 0.015


class TestSplitPages:
    def test_split_pages_partitions_all(self) -> None:
        ids = ["a", "b", "c", "d", "e"]
        result = split_pages(ids)
        all_split = set(result[SplitName.TRAIN])
        all_split.update(result[SplitName.DEV])
        all_split.update(result[SplitName.TEST])
        assert all_split == set(ids)

    def test_no_overlap_between_splits(self) -> None:
        ids = [f"page-{i}" for i in range(100)]
        result = split_pages(ids)
        train_set = set(result[SplitName.TRAIN])
        dev_set = set(result[SplitName.DEV])
        test_set = set(result[SplitName.TEST])
        assert train_set.isdisjoint(dev_set)
        assert train_set.isdisjoint(test_set)
        assert dev_set.isdisjoint(test_set)

    def test_custom_ratios(self) -> None:
        ratios = SplitRatios(train=0.5, dev=0.25, test=0.25)
        ids = [f"p-{i}" for i in range(10000)]
        result = split_pages(ids, ratios)
        total = sum(len(v) for v in result.values())
        assert abs(len(result[SplitName.TRAIN]) / total - 0.50) < 0.02
        assert abs(len(result[SplitName.DEV]) / total - 0.25) < 0.015
