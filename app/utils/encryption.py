"""Fernet-based field encryption for PII at rest.

Fails hard on startup if the key is invalid — PII must never be stored in plaintext.
"""
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_initialized = False


def _reset():
    """Reset module state — used by tests."""
    global _fernet, _initialized
    _fernet = None
    _initialized = False


def _get_fernet() -> Fernet:
    global _fernet, _initialized
    if not _initialized:
        _initialized = True
        key = settings.SENSITIVE_FIELD_KEY
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as e:
            raise RuntimeError(
                "SENSITIVE_FIELD_KEY is not a valid Fernet key. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from e
    return _fernet  # type: ignore[return-value]


def encrypt_value(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Data may be plaintext from before encryption was enabled, or from a
        # key mismatch during rotation. Log at ERROR so operators see the
        # signal — a persistent stream of these almost always indicates a key
        # rotation problem, not legacy plaintext. The key rotation CLI
        # (app/rotate_key.py) should be used to re-encrypt affected rows.
        # Mirror to the filesystem emergency log so a wave of these is
        # recoverable during incident review even without centralized logs.
        logger.error(
            "Failed to decrypt PII field — returning raw ciphertext. "
            "This usually indicates a SENSITIVE_FIELD_KEY mismatch or an "
            "un-rotated row from a previous key. Run the key rotation CLI "
            "if this persists."
        )
        # Local import keeps emergency-log import cycles impossible.
        from app.utils.emergency_log import record_critical_failure
        record_critical_failure(
            category="pii_decryption",
            message="InvalidToken while decrypting PII field",
            ciphertext_length=len(ciphertext) if ciphertext else 0,
        )
        # By default (DECRYPT_FAIL_CLOSED=1) we refuse to return raw
        # ciphertext — surface the failure to the caller so potentially
        # broken PII never silently reaches the UI or an export. Operators
        # who need reads to keep working under a key-rotation incident can
        # set DECRYPT_FAIL_CLOSED=0 to fall back to returning ciphertext.
        if settings.DECRYPT_FAIL_CLOSED == "1":
            raise RuntimeError(
                "PII decryption failed and DECRYPT_FAIL_CLOSED=1"
            ) from exc
        return ciphertext
