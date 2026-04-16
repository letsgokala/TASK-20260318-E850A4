"""Tests pinning the fail-closed modes for critical observability paths.

By default the audit middleware, quality-validation persister, and PII
decryption path now propagate failures as 5xx — an audit finding called
out the prior fail-open defaults as a "silent success" compliance gap.
Operators who need the API to remain available under dependency outages
can explicitly set ``AUDIT_FAIL_CLOSED`` / ``VALIDATION_FAIL_CLOSED`` /
``DECRYPT_FAIL_CLOSED`` to ``"0"`` to restore the previous fail-open
behavior.

These tests prove both halves of the contract: default mode raises,
fail-open mode swallows.
"""
from __future__ import annotations

import pytest

from app.utils import encryption


# ── DECRYPT_FAIL_CLOSED ───────────────────────────────────────────────────

def test_decrypt_fail_open_default_returns_ciphertext(monkeypatch):
    """Default behavior: an undecryptable value is returned as-is with an
    ERROR log and an emergency-log entry, not raised."""
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "DECRYPT_FAIL_CLOSED", "0")

    # Feed an obviously-invalid token; Fernet will raise InvalidToken.
    bad = "not-a-real-fernet-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    result = encryption.decrypt_value(bad)
    # Fail-open contract: raw ciphertext is returned so reads don't break.
    assert result == bad


def test_decrypt_fail_closed_raises(monkeypatch):
    """``DECRYPT_FAIL_CLOSED=1``: an undecryptable value must propagate."""
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "DECRYPT_FAIL_CLOSED", "1")

    bad = "not-a-real-fernet-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    with pytest.raises(RuntimeError, match="DECRYPT_FAIL_CLOSED"):
        encryption.decrypt_value(bad)


# ── AUDIT_FAIL_CLOSED ─────────────────────────────────────────────────────

def test_audit_fail_closed_settings_flag_is_readable():
    """The settings must exist and default to '1' — fail-closed — so the
    audit/decryption/validation paths propagate failures instead of silently
    succeeding. Operators must explicitly opt into fail-open by setting the
    corresponding env var to '0'."""
    from app.config import settings as _settings

    # Default must be the stringly-typed "1" — strict-string makes misconfig
    # impossible to accidentally parse as falsy (e.g. 'true' is falsy as int).
    assert _settings.AUDIT_FAIL_CLOSED == "1"
    assert _settings.DECRYPT_FAIL_CLOSED == "1"
    assert _settings.VALIDATION_FAIL_CLOSED == "1"
    assert _settings.ALERT_FAIL_CLOSED == "1"


# ── VALIDATION_FAIL_CLOSED ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validation_fail_closed_raises_when_flag_set(monkeypatch):
    """auto_validate_on_submit must raise under VALIDATION_FAIL_CLOSED=1
    when the underlying rule runner fails."""
    from app.api.v1 import quality_validation as qv
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "VALIDATION_FAIL_CLOSED", "1")

    async def _boom(*args, **kwargs):
        raise RuntimeError("rule runner exploded")

    monkeypatch.setattr(qv, "_run_all_rules", _boom)

    class _DummyReg:
        id = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(RuntimeError, match="VALIDATION_FAIL_CLOSED"):
        await qv.auto_validate_on_submit(_DummyReg(), submitted_by=None, db=None)


@pytest.mark.asyncio
async def test_validation_fail_open_default_swallows(monkeypatch):
    """Default mode: a rule-runner failure must NOT propagate."""
    from app.api.v1 import quality_validation as qv
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "VALIDATION_FAIL_CLOSED", "0")

    async def _boom(*args, **kwargs):
        raise RuntimeError("rule runner exploded")

    monkeypatch.setattr(qv, "_run_all_rules", _boom)

    class _DummyReg:
        id = "00000000-0000-0000-0000-000000000000"

    # Should not raise.
    await qv.auto_validate_on_submit(_DummyReg(), submitted_by=None, db=None)


# ── ALERT_FAIL_CLOSED ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_fail_closed_raises_when_flag_set(monkeypatch):
    """check_and_notify_breaches must raise under ALERT_FAIL_CLOSED=1 when
    the underlying query path fails, so a missing alert never translates
    into a silently-successful metrics read."""
    from app.api.v1 import metrics as metrics_mod
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "ALERT_FAIL_CLOSED", "1")

    class _BoomDB:
        async def execute(self, *a, **kw):
            raise RuntimeError("db path exploded")

    with pytest.raises(RuntimeError, match="ALERT_FAIL_CLOSED"):
        await metrics_mod.check_and_notify_breaches(_BoomDB())


@pytest.mark.asyncio
async def test_alert_fail_open_swallows_when_flag_unset(monkeypatch):
    """ALERT_FAIL_CLOSED=0: an alert-emission failure must NOT propagate,
    preserving the prior fail-open behavior for operators who opt in."""
    from app.api.v1 import metrics as metrics_mod
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "ALERT_FAIL_CLOSED", "0")

    class _BoomDB:
        async def execute(self, *a, **kw):
            raise RuntimeError("db path exploded")

    # Should not raise.
    await metrics_mod.check_and_notify_breaches(_BoomDB())
