from __future__ import annotations

from dataclasses import dataclass, field
import os
from collections.abc import Mapping
from pathlib import Path

_DEFAULT_APP_NAME = "omniocr"
_DEFAULT_DESKTOP_MODE = True
_DEFAULT_ENABLE_VLM = False
_DEFAULT_ENABLE_CALAMARI = False
_DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_DEFAULT_JOB_TIMEOUT_S = 6 * 3600
_DEFAULT_RESULT_TTL_S = 7 * 24 * 3600
_DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1")
# Public: the single source of truth for the Tesseract language stack.
# Four different defaults for this one value used to be spread across the
# codebase — "eng" in TesseractEngine and the desktop root, "grc" on the CLI,
# "grc+ell+eng" here. "eng" runs English on Greek text; "grc" alone loads no
# Latin pack, which silently disables ScriptAwareReconciler's Latin-preference
# branch (it checks provenance for a LATIN_LANGUAGE_PACKS member). The
# combined stack is the only one that makes both work.
DEFAULT_TESSERACT_LANGUAGE = "grc+ell+eng"
_DEFAULT_TESSERACT_LANGUAGE = DEFAULT_TESSERACT_LANGUAGE
_DEFAULT_KRAKEN_MODEL_PATH = ""


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = _DEFAULT_APP_NAME
    desktop_mode: bool = _DEFAULT_DESKTOP_MODE
    enable_vlm: bool = _DEFAULT_ENABLE_VLM
    enable_calamari: bool = _DEFAULT_ENABLE_CALAMARI
    max_upload_bytes: int = _DEFAULT_MAX_UPLOAD_BYTES
    redis_url: str = _DEFAULT_REDIS_URL
    # Engine selection lives here so server/cloud editions configure recognition
    # instead of hardcoding it. Both previously passed an empty Kraken model
    # path, and the server passed ``app_name`` as the Tesseract language. An
    # empty ``kraken_model_path`` now means the manifest default (see
    # ``resolve_kraken_model``).
    tesseract_language: str = _DEFAULT_TESSERACT_LANGUAGE
    kraken_model_path: str = _DEFAULT_KRAKEN_MODEL_PATH
    vlm_api_key: str | None = field(default=None, repr=False)
    # OpenAI-compatible endpoint serving Unlimited-OCR-2. Gated by
    # ``enable_vlm`` like the key above: it is a VLM, so it stays opt-in.
    unlimited_ocr_url: str | None = None
    # Server and cloud editions. ``shortcut:`` the API and the worker share
    # these directories (a named volume in docker-compose), which assumes they
    # run on one host; move uploads and results to object storage when they are
    # deployed on separate machines.
    upload_dir: Path = Path("uploads")
    result_dir: Path = Path("results")
    # A book takes hours: the queue's own timeout must not cut it short.
    job_timeout_s: int = _DEFAULT_JOB_TIMEOUT_S
    result_ttl_s: int = _DEFAULT_RESULT_TTL_S
    # ``(api_key, tenant)`` pairs from ``OMNIOCR_API_KEYS="key:tenant,..."``.
    # No keys configured means every request is refused, not allowed.
    api_keys: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    cors_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = _DEFAULT_ALLOWED_HOSTS

    def __post_init__(self) -> None:
        if not self.app_name.strip():
            raise ValueError("app_name must not be empty")
        if self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if not self.redis_url.strip():
            raise ValueError("redis_url must not be empty")
        if self.job_timeout_s <= 0 or self.result_ttl_s <= 0:
            raise ValueError("job_timeout_s and result_ttl_s must be positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Load validated settings from ``OMNIOCR_*`` environment variables."""
        values = environ if environ is not None else os.environ
        return cls(
            app_name=values.get("OMNIOCR_APP_NAME", _DEFAULT_APP_NAME),
            desktop_mode=_env_bool(values, "OMNIOCR_DESKTOP_MODE", _DEFAULT_DESKTOP_MODE),
            enable_vlm=_env_bool(values, "OMNIOCR_ENABLE_VLM", _DEFAULT_ENABLE_VLM),
            enable_calamari=_env_bool(values, "OMNIOCR_ENABLE_CALAMARI", _DEFAULT_ENABLE_CALAMARI),
            max_upload_bytes=_env_int(
                values, "OMNIOCR_MAX_UPLOAD_BYTES", _DEFAULT_MAX_UPLOAD_BYTES
            ),
            redis_url=values.get("OMNIOCR_REDIS_URL", _DEFAULT_REDIS_URL),
            tesseract_language=values.get(
                "OMNIOCR_TESSERACT_LANGUAGE", _DEFAULT_TESSERACT_LANGUAGE
            ),
            kraken_model_path=values.get("OMNIOCR_KRAKEN_MODEL_PATH", _DEFAULT_KRAKEN_MODEL_PATH),
            vlm_api_key=values.get("OMNIOCR_VLM_API_KEY"),
            unlimited_ocr_url=values.get("OMNIOCR_UNLIMITED_OCR_URL"),
            upload_dir=Path(values.get("OMNIOCR_UPLOAD_DIR", "uploads")),
            result_dir=Path(values.get("OMNIOCR_RESULT_DIR", "results")),
            job_timeout_s=_env_int(values, "OMNIOCR_JOB_TIMEOUT_S", _DEFAULT_JOB_TIMEOUT_S),
            result_ttl_s=_env_int(values, "OMNIOCR_RESULT_TTL_S", _DEFAULT_RESULT_TTL_S),
            api_keys=_env_api_keys(values.get("OMNIOCR_API_KEYS", "")),
            cors_origins=_env_list(values.get("OMNIOCR_CORS_ORIGINS", "")),
            allowed_hosts=_env_list(values.get("OMNIOCR_ALLOWED_HOSTS", ""))
            or _DEFAULT_ALLOWED_HOSTS,
        )


def _env_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_api_keys(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse ``key:tenant,key:tenant``; a key may not contain ``:``."""
    pairs: list[tuple[str, str]] = []
    for item in _env_list(raw):
        key, separator, tenant = item.partition(":")
        if not separator or not key.strip() or not tenant.strip():
            raise ValueError("OMNIOCR_API_KEYS entries must look like key:tenant")
        pairs.append((key.strip(), tenant.strip()))
    return tuple(pairs)


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


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be an integer value") from exc
