"""pytest configuration and shared fixtures for OmniOCR tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from omniocr.testing.fixtures import set_corpus_path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packages" / "omniocr" / "src"

for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Point fixture helpers at the repository's corpus directory.
set_corpus_path(ROOT / "tests" / "corpus")


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--runslow``.

    The ``slow`` marker was registered in pyproject and applied to the Kraken
    inference tests, and their docstrings told the reader to run them with
    ``--runslow`` — but the option and the skip hook were never written, so
    the marker skipped nothing and the flag was an error. The tests ran on
    every invocation, taking minutes, and one of them was only tolerable
    because ``xfail(strict=False)`` was swallowing its failures.
    """
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run tests marked 'slow'"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="slow: Kraken/training inference; pass --runslow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
