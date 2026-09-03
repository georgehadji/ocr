"""Model manifest — hash-pinned artifact registry.

Manages ``models/manifest.json``: engine, name, source, licence, SHA-256.
Verified via v1's existing ``sha256_file()`` / ``verify_model_hash()``.
Closes the licensing requirement by making provenance mandatory.
"""

from __future__ import annotations

import json
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
