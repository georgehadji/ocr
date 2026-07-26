from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of a model artifact without loading it all at once."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_hash(path: str | Path, expected_hash: str) -> bool:
    """Verify an artifact against a lowercase or uppercase hexadecimal digest."""
    if len(expected_hash) != 64:
        raise ValueError("expected_hash must be a SHA-256 hexadecimal digest")
    try:
        int(expected_hash, 16)
    except ValueError as exc:
        raise ValueError("expected_hash must be a SHA-256 hexadecimal digest") from exc
    return sha256_file(path).lower() == expected_hash.lower()


__all__ = ["sha256_file", "verify_model_hash"]
