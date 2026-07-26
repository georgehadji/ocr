from __future__ import annotations

from pathlib import PurePath

from omniocr.domain.errors import IngestError
from omniocr.domain.result import Err, Ok, Result


_MAGIC_BYTES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
}


def validate_upload(data: bytes, filename: str, max_bytes: int) -> Result[bytes, IngestError]:
    """Validate an uploaded PDF/image before it reaches an adapter."""
    if max_bytes <= 0:
        return Err(IngestError("upload size limit must be positive"))
    if not data:
        return Err(IngestError("upload is empty"))
    if len(data) > max_bytes:
        return Err(IngestError("upload exceeds the configured size limit"))

    path = PurePath(filename)
    if path.name != filename or path.name in {".", ".."}:
        return Err(IngestError("upload filename must not contain path components"))
    suffix = path.suffix.lower()
    signatures = _MAGIC_BYTES.get(suffix)
    if signatures is None:
        return Err(IngestError("unsupported upload format"))
    if not any(data.startswith(signature) for signature in signatures):
        return Err(IngestError("upload content does not match its filename"))
    return Ok(data)


__all__ = ["validate_upload"]
