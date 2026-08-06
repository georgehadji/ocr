"""Device selection: use an accelerator when one is usable, CPU otherwise."""

import logging
import sys
from types import SimpleNamespace

import pytest

from omniocr.infrastructure.device import is_accelerator, select_device


def _fake_torch(
    cuda_available: object = False,
    mps_available: object = False,
) -> SimpleNamespace:
    """A torch stub. Pass a callable that raises to simulate a probe blowing up."""
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=cuda_available if callable(cuda_available) else lambda: cuda_available
        ),
        backends=SimpleNamespace(
            mps=SimpleNamespace(
                is_available=mps_available if callable(mps_available) else lambda: mps_available
            )
        ),
    )


class TestSelectDevice:
    def test_prefers_cuda_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))

        assert select_device() == "cuda:0"

    def test_cuda_answer_carries_an_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``ketos`` splits the device string on ":" and indexes [1] unconditionally.

        A bare "cuda" therefore raises ``IndexError`` inside
        ``kraken.ketos.util.to_ptl_device`` — fine-tuning, the one place a GPU
        matters most, would be the only place that crashed.
        """
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))

        assert select_device().split(":")[1] == "0"

    def test_uses_mps_when_there_is_no_cuda(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Apple Silicon has a GPU; it is just not a CUDA one."""
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(mps_available=True))

        assert select_device() == "mps"

    def test_falls_back_to_cpu_when_no_accelerator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "torch", _fake_torch())

        assert select_device() == "cpu"

    def test_falls_back_to_cpu_when_torch_is_absent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The core install has no torch at all — that is a CPU answer, not an error.

        The log assertion is load-bearing: "cpu" is also the answer on every
        other path, so without it this test passes even if the import branch
        never runs.
        """
        monkeypatch.delitem(sys.modules, "torch", raising=False)
        monkeypatch.setattr(
            "builtins.__import__",
            _import_failing_on("torch", ImportError("No module named 'torch'")),
        )

        with caplog.at_level(logging.DEBUG, logger="omniocr.device"):
            assert select_device() == "cpu"

        assert "torch unavailable" in caplog.text

    def test_falls_back_to_cpu_when_cuda_probe_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A driver mismatch raises rather than returning False — still not fatal."""

        def _boom() -> bool:
            raise RuntimeError("CUDA driver version is insufficient")

        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=_boom))

        assert select_device() == "cpu"

    def test_a_raising_cuda_probe_still_lets_mps_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One broken probe must not veto the other accelerator."""

        def _boom() -> bool:
            raise RuntimeError("CUDA driver version is insufficient")

        monkeypatch.setitem(
            sys.modules, "torch", _fake_torch(cuda_available=_boom, mps_available=True)
        )

        assert select_device() == "mps"

    def test_falls_back_to_cpu_when_mps_attribute_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Older torch builds have no ``backends.mps`` — an AttributeError, not a crash."""
        monkeypatch.setitem(
            sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        )

        assert select_device() == "cpu"

    def test_explicit_preference_short_circuits_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True))

        assert select_device("cpu") == "cpu"


class TestIsAccelerator:
    @pytest.mark.parametrize("device", ["cuda:0", "cuda:1", "mps"])
    def test_recognizes_accelerators(self, device: str) -> None:
        assert is_accelerator(device) is True

    def test_cpu_is_not_an_accelerator(self) -> None:
        assert is_accelerator("cpu") is False


def _import_failing_on(name: str, error: Exception) -> object:
    """Return an ``__import__`` replacement that raises for one module name."""
    real = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _fake(module: str, *args: object, **kwargs: object) -> object:
        if module == name:
            raise error
        return real(module, *args, **kwargs)

    return _fake
