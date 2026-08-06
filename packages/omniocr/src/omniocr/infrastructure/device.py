"""Compute-device selection shared by every torch-backed adapter.

This lives here rather than in ``kraken.py`` because more than the recognizer
needs it: ``ketos`` fine-tuning is the most GPU-sensitive step in the project,
and ``omniocr doctor`` reports the answer before either runs.

The returned string must be valid in **two** dialects at once:

* ``torch`` / ``kraken.lib.models.load_any(device=...)`` — accepts ``cpu``,
  ``mps``, ``cuda``, ``cuda:0``.
* the ``ketos`` CLI (``kraken.ketos.util.to_ptl_device``) — accepts ``cpu``,
  ``mps``, ``cuda:N``, and raises ``IndexError`` on a bare ``cuda`` because it
  splits on ``:`` and indexes ``[1]`` unconditionally.

So the CUDA answer is ``cuda:0``, never ``cuda`` — the intersection of both
grammars. Returning the shorter form would work everywhere except the one
place a GPU matters most.
"""

from __future__ import annotations

import logging

_LOG = logging.getLogger("omniocr.device")

CPU = "cpu"


def select_device(preferred: str | None = None) -> str:
    """Return the compute device to use: an accelerator when usable, else CPU.

    ``preferred`` short-circuits detection, so a caller can pin a device (and
    tests can exercise every branch on a machine with no GPU).

    Availability is probed defensively: a torch build without CUDA, a driver
    mismatch, or a machine with no GPU can each *raise* here rather than
    returning ``False``, and none of those is a reason to fail the run.
    """
    if preferred is not None:
        return preferred
    try:
        import torch
    except Exception as exc:  # ImportError, or a broken torch install
        _LOG.debug("torch unavailable, using CPU: %s", exc)
        return CPU

    try:
        if torch.cuda.is_available():
            return "cuda:0"
    except Exception as exc:  # driver/CUDA init failures
        _LOG.debug("CUDA probe failed: %s", exc)

    try:
        if torch.backends.mps.is_available():
            return "mps"
    except Exception as exc:  # no MPS on this build/platform
        _LOG.debug("MPS probe failed: %s", exc)

    return CPU


def is_accelerator(device: str) -> bool:
    """True when ``device`` is a GPU, so callers can decide to retry on CPU."""
    return not device.startswith(CPU)


def device_note() -> str | None:
    """Explain a CPU answer when the cause is fixable, else ``None``.

    "cpu" has two very different meanings — no GPU in the machine, or a GPU
    the installed torch wheel simply cannot address. The second is a one-command
    fix and the first is not, so a bare "device: cpu" from ``doctor`` is the
    wrong amount of information.
    """
    if is_accelerator(select_device()):
        return None
    try:
        import torch
    except Exception:
        return (
            "torch is not installed — GPU acceleration is unavailable (CPU is fine, just slower)."
        )
    if torch.version.cuda is None and not _mps_built():
        return (
            f"torch {torch.__version__} is a CPU-only build. If this machine has an "
            "NVIDIA GPU, install a CUDA wheel from https://pytorch.org to use it."
        )
    return None


def _mps_built() -> bool:
    try:
        import torch

        return bool(torch.backends.mps.is_built())
    except Exception:
        return False


__all__ = ["CPU", "device_note", "is_accelerator", "select_device"]
