"""Model manifest — hash-pinned artifact registry.

Manages ``models/manifest.json``: engine, name, source, licence, SHA-256.
Verified via v1's existing ``sha256_file()`` / ``verify_model_hash()``.
Closes the licensing requirement by making provenance mandatory.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from omniocr.infrastructure.models import sha256_file

# Where model artifacts and their manifest live, relative to the working
# directory. The CLI used to keep its own ``Path("models")`` alongside this
# module's own default path string; both are now derived from these.
MODELS_DIR = Path("models")
MANIFEST_FILENAME = "manifest.json"
MANIFEST_PATH = MODELS_DIR / MANIFEST_FILENAME


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """A single model entry in the manifest."""

    engine: str
    name: str
    source: str
    licence: str
    sha256: str
    description: str = ""
    params: tuple[str, ...] = field(default_factory=tuple)
    # Marks the model to reach for when the caller names none. Measured, not
    # assumed: on page 31 of the target document the three bundled Kraken
    # models scored 0.038, 0.147 and 0.264 CER. Selection had been
    # `sorted(glob(...))[0]`, which picked the 0.264 one — 4.7x worse than
    # Tesseract on the same page — purely because of its filename.
    default: bool = False


class ModelManifest:
    """Hash-pinned manifest of known models.

    Reads/writes ``models/manifest.json``. Each entry is validated against
    the actual artifact on disk via SHA-256.
    """

    def __init__(self, manifest_path: str | Path = MANIFEST_PATH) -> None:
        self._path = Path(manifest_path)
        self._entries: dict[str, ManifestEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load entries from the manifest file."""
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("models", []):
                entry = ManifestEntry(
                    engine=item["engine"],
                    name=item["name"],
                    source=item.get("source", ""),
                    licence=item.get("licence", ""),
                    sha256=item["sha256"],
                    description=item.get("description", ""),
                    params=tuple(item.get("params", [])),
                    default=bool(item.get("default", False)),
                )
                self._entries[entry.name] = entry
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    def default_for(self, engine: str) -> ManifestEntry | None:
        """Return the model to use for ``engine`` when the caller names none.

        ``None`` when the manifest declares no default, so a caller can fall
        back rather than fail — but it should say what it is falling back to.
        """
        for entry in self._entries.values():
            if entry.engine == engine and entry.default:
                return entry
        return None

    def add(self, entry: ManifestEntry) -> None:
        """Add or update a manifest entry."""
        self._entries[entry.name] = entry
        self._save()

    def get(self, name: str) -> ManifestEntry | None:
        """Look up an entry by model name."""
        return self._entries.get(name)

    def all(self) -> Sequence[ManifestEntry]:
        """Return all entries."""
        return tuple(self._entries.values())

    def verify(self, name: str, path: Path) -> bool:
        """Verify a model artifact against its manifest entry."""
        entry = self._entries.get(name)
        if entry is None:
            return False
        if not path.is_file():
            return False
        return sha256_file(path) == entry.sha256

    def _save(self) -> None:
        """Persist entries to the manifest file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "models": [
                {
                    "engine": e.engine,
                    "name": e.name,
                    "source": e.source,
                    "licence": e.licence,
                    "sha256": e.sha256,
                    "description": e.description,
                    "params": list(e.params),
                    "default": e.default,
                }
                for e in self._entries.values()
            ]
        }
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


__all__ = ["ManifestEntry", "ModelManifest"]


_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"


def is_lfs_pointer(path: Path) -> bool:
    """True when ``path`` is a Git LFS pointer file standing in for the real artifact.

    A checkout without ``git lfs pull`` leaves a ~130-byte text stub where each
    model should be. Kraken cannot load it, and the hash check alone reports only
    a mismatch, which does not say why.
    """
    try:
        with path.open("rb") as stream:
            return stream.read(len(_LFS_POINTER_PREFIX)) == _LFS_POINTER_PREFIX
    except OSError:
        return False


def resolve_kraken_model(explicit: str | None = None, models_dir: Path = MODELS_DIR) -> str:
    """Choose the Kraken model: explicit, else the manifest default.

    Selection used to be ``sorted(glob("*.mlmodel"))[0]`` — alphabetical, so
    the choice was decided by a filename. That is not a detail. Measured on
    page 31 of the target document, the three bundled models score 0.038,
    0.147 and 0.264 CER, and the alphabetically first one is the 0.264 —
    4.7x worse than Tesseract on the same page, while the best is 1.5x
    better. The manifest now says which one to use and why.

    Lives here, not in the CLI, so the server, cloud and desktop editions get
    the same choice: they passed an empty path, which Kraken read as the
    working directory.
    """
    if explicit is not None:
        return explicit
    candidates = sorted(models_dir.glob("*.mlmodel"))
    if not candidates:
        raise FileNotFoundError(
            f"no Kraken model found in {models_dir}/ — pass --model or see models/README.md"
        )
    preferred = ModelManifest(models_dir / MANIFEST_FILENAME).default_for("kraken")
    if preferred is not None:
        chosen = models_dir / preferred.name
        if chosen.is_file():
            return str(chosen)
        print(
            f"manifest default {preferred.name} is missing from {models_dir}/ — "
            f"falling back to {candidates[0].name}",
            file=sys.stderr,
        )
    return str(candidates[0])
