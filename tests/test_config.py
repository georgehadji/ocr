from __future__ import annotations

import pytest

from omniocr.infrastructure.config import Settings


def test_settings_use_safe_defaults() -> None:
    settings = Settings.from_env({})

    assert settings.app_name == "omniocr"
    assert settings.desktop_mode
    assert not settings.enable_vlm
    assert settings.max_upload_bytes == 2 * 1024 * 1024 * 1024
    assert settings.vlm_api_key is None


def test_settings_load_boolean_limits_and_secret_from_environment() -> None:
    settings = Settings.from_env(
        {
            "OMNIOCR_DESKTOP_MODE": "false",
            "OMNIOCR_ENABLE_VLM": "true",
            "OMNIOCR_MAX_UPLOAD_BYTES": "4096",
            "OMNIOCR_VLM_API_KEY": "secret",
        }
    )

    assert not settings.desktop_mode
    assert settings.enable_vlm
    assert settings.max_upload_bytes == 4096
    assert settings.vlm_api_key == "secret"
    assert "secret" not in repr(settings)


def test_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="boolean"):
        Settings.from_env({"OMNIOCR_ENABLE_VLM": "maybe"})
    with pytest.raises(ValueError, match="positive"):
        Settings(max_upload_bytes=0)


def test_settings_reject_non_numeric_max_upload_bytes() -> None:
    """Proof-of-defect D1: non-numeric OMNIOCR_MAX_UPLOAD_BYTES → helpful ValueError."""
    for bad in ["abc", "", "3.14"]:
        with pytest.raises(ValueError, match="integer"):
            Settings.from_env({"OMNIOCR_MAX_UPLOAD_BYTES": bad})


def test_settings_accept_valid_max_upload_bytes() -> None:
    """Innocence test: valid integer values parse correctly."""
    s = Settings.from_env({"OMNIOCR_MAX_UPLOAD_BYTES": "65536"})
    assert s.max_upload_bytes == 65536
