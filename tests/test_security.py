from __future__ import annotations

from omniocr.infrastructure.security import validate_upload


def test_upload_validation_accepts_matching_png_magic_bytes() -> None:
    data = b"\x89PNG\r\n\x1a\nimage"

    result = validate_upload(data, "page.png", 1024)

    assert result.is_ok()
    assert result.value == data


def test_upload_validation_rejects_size_path_and_magic_mismatches() -> None:
    assert validate_upload(b"%PDF-1.7", "book.pdf", 2).is_err()
    assert validate_upload(b"%PDF-1.7", "../book.pdf", 1024).is_err()
    assert validate_upload(b"not-pdf", "book.pdf", 1024).is_err()
    assert validate_upload(b"data", "book.exe", 1024).is_err()
