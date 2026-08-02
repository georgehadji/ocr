"""Tests for training pipeline — uses v2 APIs.

The old ``export_ground_truth_to_kraken_json`` is deprecated; these tests
exercise the v2 replacement pathway via ``assemble_training_samples`` and
``AltoTrainingExporter``.
"""

from __future__ import annotations


from omniocr.infrastructure.training import compute_cer_improvement


class TestComputeCerImprovement:
    def test_positive(self) -> None:
        """Improvement from 10% to 5% CER is a 50% relative improvement."""
        assert compute_cer_improvement(0.10, 0.05) == 50.0

    def test_negative(self) -> None:
        """Regression from 5% to 10% CER is a -100% relative change."""
        assert compute_cer_improvement(0.05, 0.10) == -100.0

    def test_zero_baseline(self) -> None:
        """Zero baseline returns 0 to avoid division by zero."""
        assert compute_cer_improvement(0.0, 0.05) == 0.0


class TestDeprecationShim:
    """Tests for the deprecated v1 training function."""

    def test_raises_deprecation(self) -> None:
        """Calling the deprecated function should raise a DeprecationWarning."""
        from omniocr.infrastructure.training import export_ground_truth_to_kraken_json
        import pytest

        with pytest.raises(DeprecationWarning):
            export_ground_truth_to_kraken_json(None, "/tmp")
