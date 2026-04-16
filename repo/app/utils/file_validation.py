"""File-type validation using magic-byte signature inspection.

The audit flagged that backend file-type checks trusted only the
client-declared ``content_type`` header, which is trivially spoofable.
This module provides a shared helper that:

1. Checks the declared MIME type / extension against the allow-list.
2. Inspects the first bytes of the file content ("magic bytes") and
   rejects mismatches (e.g. content-type says PDF but the bytes are
   arbitrary text).

Used by both material uploads (``app/api/v1/materials.py``) and
invoice uploads (``app/api/v1/finance.py``).
"""
from __future__ import annotations

from fastapi import HTTPException, status

# Canonical magic-byte signatures for the three allowed file types.
_SIGNATURES: dict[str, list[bytes]] = {
    "application/pdf": [b"%PDF-"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
}

# Reverse map: for each prefix, what MIME type(s) it indicates.
_PREFIX_TO_MIME: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
]

_ALLOWED_MIME_TYPES = frozenset(_SIGNATURES.keys())


def validate_file_content(
    content: bytes,
    declared_mime: str | None,
    *,
    context: str = "file",
) -> None:
    """Validate ``content`` against both MIME-type and magic-byte rules.

    Raises ``HTTPException(415)`` if:
    - ``declared_mime`` is not in the allow-list, or
    - the file content does not start with the expected magic bytes for
      the declared MIME type.

    Parameters
    ----------
    content:
        The raw file bytes (only the first ~16 bytes are inspected).
    declared_mime:
        The MIME type the client declared (e.g. ``file.content_type``).
    context:
        A human-readable noun for the error message (``"material"`` or
        ``"invoice"``).
    """
    if declared_mime not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"{context.capitalize()} file type '{declared_mime}' not allowed. "
                "Allowed: PDF, JPG, PNG."
            ),
        )

    expected_sigs = _SIGNATURES[declared_mime]
    if not any(content[:len(sig)].startswith(sig) for sig in expected_sigs):
        # Sniff what the bytes actually look like.
        sniffed = _sniff_type(content)
        sniffed_label = sniffed or "unknown/binary"
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"{context.capitalize()} file content does not match "
                f"declared type '{declared_mime}'. "
                f"File signature indicates '{sniffed_label}'. "
                "Upload the correct file or fix the file extension."
            ),
        )


def _sniff_type(content: bytes) -> str | None:
    """Best-effort type sniffing from the leading bytes."""
    for prefix, mime in _PREFIX_TO_MIME:
        if content[:len(prefix)].startswith(prefix):
            return mime
    return None
