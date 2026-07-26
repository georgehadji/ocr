from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omniocr.infrastructure.models import sha256_file, verify_model_hash


def test_model_hash_helpers_verify_artifact() -> None:
    artifact = Path("pyproject.toml")
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert sha256_file(artifact) == expected
    assert verify_model_hash(artifact, expected.upper())
    assert not verify_model_hash(artifact, "0" * 64)


def test_model_hash_verifier_rejects_malformed_digest() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        verify_model_hash(Path("pyproject.toml"), "not-a-hash")
