from __future__ import annotations

from dataclasses import dataclass, field
import os
from collections.abc import Mapping

_DEFAULT_APP_NAME = "omniocr"
_DEFAULT_DESKTOP_MODE = True
_DEFAULT_ENABLE_VLM = False
_DEFAULT_ENABLE_CALAMARI = False
_DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = _DEFAULT_APP_NAME
    desktop_mode: bool = _DEFAULT_DESKTOP_MODE
    enable_vlm: bool = _DEFAULT_ENABLE_VLM
    enable_calamari: bool = _DEFAULT_ENABLE_CALAMARI
    max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES
    vlm_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.app_name.strip():
            raise ValueError("app_name must not be empty")
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Load validated settings from ``OMNIOCR_*`` environment variables."""
        values = environ if environ is not None else os.environ
        return cls(
            app_name=values.get("OMNIOCR_APP_NAME", _DEFAULT_APP_NAME),
            desktop_mode=_env_bool(values, "OMNIOCR_DESKTOP_MODE", _DEFAULT_DESKTOP_MODE),
            enable_vlm=_env_bool(values, "OMNIOCR_ENABLE_VLM", _DEFAULT_ENABLE_VLM),
            enable_calamari=_env_bool(values, "OMNIOCR_ENABLE_CALAMARI", _DEFAULT_ENABLE_CALAMARI),
            max_upload_bytes=int(
                values.get("OMNIOCR_MAX_UPLOAD_BYTES", str(_DEFAULT_MAX_UPLOAD_BYTES))
            ),
            vlm_api_key=values.get("OMNIOCR_VLM_API_KEY"),
        )


def _env_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")
