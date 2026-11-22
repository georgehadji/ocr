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


@pytest.mark.parametrize(
    "settings",
    [
        Settings(),
        Settings(enable_vlm=True, vlm_api_key="k"),
        Settings(enable_vlm=True, unlimited_ocr_url="http://gpu:8000/v1"),
    ],
)
def test_a_consistent_engine_set_has_no_problems(settings: Settings) -> None:
    assert settings.problems() == ()


def test_vlm_switched_on_with_nothing_to_call_is_a_problem() -> None:
    (problem,) = Settings(enable_vlm=True).problems()

    assert "OMNIOCR_ENABLE_VLM" in problem
    assert "OMNIOCR_VLM_API_KEY" in problem


@pytest.mark.parametrize(
    "settings, name",
    [
        (Settings(vlm_api_key="k"), "OMNIOCR_VLM_API_KEY"),
        (Settings(unlimited_ocr_url="http://gpu:8000/v1"), "OMNIOCR_UNLIMITED_OCR_URL"),
    ],
)
def test_a_vlm_setting_given_while_vlm_is_off_is_a_problem(settings: Settings, name: str) -> None:
    (problem,) = settings.problems()

    assert name in problem
    assert "OMNIOCR_ENABLE_VLM" in problem


def test_a_problem_never_quotes_the_key() -> None:
    assert "hunter2" not in " ".join(Settings(vlm_api_key="hunter2").problems())


def test_an_illegal_combination_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        settings = Settings(enable_vlm=True)

    assert settings.enable_vlm
    assert [r.getMessage() for r in caplog.records] == list(settings.problems())


def test_a_consistent_engine_set_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        Settings()

    assert caplog.records == []


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


def test_max_workers_defaults_to_sequential_and_reads_the_environment() -> None:
    assert Settings.from_env({}).max_workers == 1
    assert Settings.from_env({"OMNIOCR_MAX_WORKERS": "4"}).max_workers == 4


def test_max_workers_rejects_zero_negative_and_non_numeric() -> None:
    for bad in ["0", "-2"]:
        with pytest.raises(ValueError, match="at least 1"):
            Settings.from_env({"OMNIOCR_MAX_WORKERS": bad})
    with pytest.raises(ValueError, match="integer"):
        Settings.from_env({"OMNIOCR_MAX_WORKERS": "many"})


def test_settings_accept_valid_max_upload_bytes() -> None:
    """Innocence test: valid integer values parse correctly."""
    s = Settings.from_env({"OMNIOCR_MAX_UPLOAD_BYTES": "65536"})
    assert s.max_upload_bytes == 65536
