"""Tests for domain/corpus.py — corpus value objects."""

from __future__ import annotations

from pathlib import Path

import pytest

from omniocr.domain.corpus import CorpusPage, SplitName, SplitRatios
from omniocr.domain.models import Script


class TestCorpusPage:
    def test_valid_page(self, tmp_path: Path) -> None:
        img = tmp_path / "page.png"
        img.write_text("img")
        gt = tmp_path / "page.txt"
        gt.write_text("gt")
        page = CorpusPage(
            page_id="ancient-1",
            image_path=img,
            ground_truth_path=gt,
            script=Script.ANCIENT,
            split=SplitName.TRAIN,
            provenance="public-domain-source",
        )
        assert page.page_id == "ancient-1"
        assert page.script == Script.ANCIENT
        assert page.provenance == "public-domain-source"
        assert not page.is_synthetic

    def test_missing_image_raises(self, tmp_path: Path) -> None:
        gt = tmp_path / "gt.txt"
        gt.write_text("gt")
        with pytest.raises(ValueError, match="image_path does not exist"):
            CorpusPage(
                page_id="p1",
                image_path=tmp_path / "missing.png",
                ground_truth_path=gt,
                script=Script.MODERN,
                split=SplitName.TRAIN,
                provenance="test",
            )

    def test_missing_ground_truth_raises(self, tmp_path: Path) -> None:
        img = tmp_path / "img.png"
        img.write_text("img")
        with pytest.raises(ValueError, match="ground_truth_path does not exist"):
            CorpusPage(
                page_id="p1",
                image_path=img,
                ground_truth_path=tmp_path / "missing.txt",
                script=Script.MODERN,
                split=SplitName.TRAIN,
                provenance="test",
            )

    def test_empty_provenance_raises(self, tmp_path: Path) -> None:
        img = tmp_path / "img.png"
        img.write_text("img")
        gt = tmp_path / "gt.txt"
        gt.write_text("gt")
        with pytest.raises(ValueError, match="provenance must not be empty"):
            CorpusPage(
                page_id="p1",
                image_path=img,
                ground_truth_path=gt,
                script=Script.MODERN,
                split=SplitName.TRAIN,
                provenance="",
            )


class TestSplitRatios:
    def test_default_ratios(self) -> None:
        r = SplitRatios()
        assert abs(r.train - 0.80) < 1e-9
        assert abs(r.dev - 0.10) < 1e-9
        assert abs(r.test - 0.10) < 1e-9

    def test_custom_ratios(self) -> None:
        r = SplitRatios(train=0.5, dev=0.25, test=0.25)
        assert abs(r.train - 0.50) < 1e-9

    def test_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="must sum to 1.0"):
            SplitRatios(train=0.5, dev=0.5, test=0.5)
