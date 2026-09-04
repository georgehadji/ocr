"""Guard the torch/torchvision pairing that Kraken silently depends on.

Kraken is the accuracy driver, and it defers its torch imports — so
``import kraken`` succeeds against a torch stack that cannot process a
single page. That is exactly what happened on 2026-09-04: torch was
reinstalled from the cu124 index, which tops out at 2.6, while torchvision
stayed at a build for 2.10. Every page then failed with ``operator
torchvision::nms does not exist``, a message that names neither the cause
nor the fix, and ``omniocr doctor`` reported the install healthy.

CI cannot catch the real thing — it does not install the kraken extra, so
a green build is not evidence Kraken runs. These tests catch the two halves
that are checkable anywhere: that the probe reports a broken pairing
honestly, and that a broken pairing reaches the operator as a problem with
a remediation rather than as a silent pass.
"""

from __future__ import annotations

import importlib.util

import pytest

from omniocr.interfaces.cli import _environment, _environment_problems, _torch_stack_report

_HAS_TORCH = importlib.util.find_spec("torch") is not None


def _env(stack: dict[str, object]) -> dict[str, object]:
    """A minimal healthy environment with the torch stack swapped in."""
    return {
        "tesseract": {"available": True, "path": "/usr/bin/tesseract", "languages": ["ell", "grc"]},
        "kraken": {"installed": True, "models": ["models/x.mlmodel"]},
        "torch_stack": stack,
        "device": {"selected": "cpu"},
        "extras": {"pdf": True, "docx": True, "opencv": True, "pillow": True},
    }


def test_environment_actually_reports_the_torch_stack() -> None:
    """Pin the contract that lets `_environment_problems` tolerate the key's absence.

    That tolerance exists for callers built before this check (the CLI tests
    construct partial environment dicts). It would quietly disable the check
    if `_environment` ever stopped populating the key, so assert it does.
    """
    assert "torch_stack" in _environment()


def test_absent_torch_is_not_a_problem() -> None:
    """torch is only pulled in by the kraken extra — its absence is a choice."""
    assert _environment_problems(_env({"installed": False})) == []


def test_coherent_stack_is_not_a_problem() -> None:
    stack = {"installed": True, "torch": "2.6.0", "torchvision": "0.21.0", "coherent": True}
    assert _environment_problems(_env(stack)) == []


def test_incoherent_stack_is_reported_with_a_remediation() -> None:
    """The failure operators actually hit must name the fix, not just the symptom.

    ``operator torchvision::nms does not exist`` is unactionable on its own.
    Whoever reads it needs to know both packages have to be reinstalled from
    a single index.
    """
    stack = {
        "installed": True,
        "torch": "2.6.0+cu124",
        "torchvision": None,
        "coherent": False,
        "error": "operator torchvision::nms does not exist",
    }
    problems = _environment_problems(_env(stack))

    assert len(problems) == 1
    message = problems[0]
    assert "torchvision" in message
    assert "Kraken cannot run" in message
    assert "--index-url" in message, "the message must carry the fix, not just the symptom"


@pytest.mark.skipif(not _HAS_TORCH, reason="torch is not installed")
def test_probe_agrees_with_reality_on_this_machine() -> None:
    """The probe must not report 'coherent' on a stack that cannot run.

    This is the assertion that would have failed while the local install was
    broken, and the one that fails again if the pairing drifts.
    """
    report = _torch_stack_report()
    assert report["installed"] is True
    assert report["coherent"] is True, (
        f"torch {report.get('torch')} and torchvision are ABI-incompatible: "
        f"{report.get('error')} — Kraken cannot run in this environment"
    )
