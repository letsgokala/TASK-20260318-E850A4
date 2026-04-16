"""Tests for the filesystem emergency log used as a fallback for critical
failures that are intentionally suppressed in the API path.

These tests pin three behaviors the audit specifically called for:

1. A critical failure writes a JSON line to the configured path — so it is
   recoverable even in deployments without centralized logging.
2. Non-JSON-serializable context values don't blow up the writer.
3. A broken destination path does not propagate the exception — the writer
   must never cause a secondary failure on top of the original one.
"""
import json
import os

import pytest

from app.utils import emergency_log


def test_emergency_log_writes_jsonl(tmp_path, monkeypatch):
    target = tmp_path / "critical.jsonl"
    monkeypatch.setenv(emergency_log._PATH_OVERRIDE_ENV, str(target))

    emergency_log.record_critical_failure(
        category="audit_middleware",
        message="failed to persist",
        action="POST /api/v1/reports/generate/audit",
        user_id="abc-123",
    )
    emergency_log.record_critical_failure(
        category="alert_emission",
        message="notifier exploded",
    )

    lines = target.read_text().strip().split("\n")
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["category"] == "audit_middleware"
    assert first["message"] == "failed to persist"
    assert first["user_id"] == "abc-123"
    assert "ts" in first

    second = json.loads(lines[1])
    assert second["category"] == "alert_emission"


def test_emergency_log_handles_unserializable_values(tmp_path, monkeypatch):
    """A context value that can't be JSON-serialized must be coerced to str
    so the writer never raises from inside a failure-handling branch."""
    target = tmp_path / "critical.jsonl"
    monkeypatch.setenv(emergency_log._PATH_OVERRIDE_ENV, str(target))

    class NotJson:
        def __repr__(self):
            return "<NotJson instance>"

    emergency_log.record_critical_failure(
        category="audit_middleware",
        message="object context",
        weird=NotJson(),
    )

    record = json.loads(target.read_text().strip())
    assert record["weird"] == "<NotJson instance>"


def test_emergency_log_swallows_write_errors(monkeypatch, caplog):
    """If the configured path cannot be written, the helper must log a
    warning and return normally — NEVER propagate the exception.

    We force a write failure by patching ``os.makedirs`` to raise ``OSError``;
    that mirrors what happens in restrictive containers where the target
    directory cannot be created (e.g., read-only root filesystem).
    """
    import os as _os

    def _boom(*args, **kwargs):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(_os, "makedirs", _boom)

    # Should not raise.
    emergency_log.record_critical_failure(
        category="pii_decryption",
        message="swallow-test",
    )
