"""Filesystem fallback for critical failures that must not be silently lost.

The audit middleware, metrics alert emitter, validation persister, and PII
decryption path all wrap their DB work in try/except so a downstream failure
doesn't kill the user-facing API response. Pure logging isn't enough: if a
deployment has no centralized log sink configured, those ``logger.error``
lines can vanish. This module writes a newline-delimited JSON record of the
failure to a local file at ``EMERGENCY_LOG_PATH`` (default
``/var/log/eagle_point/critical_failures.jsonl``) so operators reviewing a
host after an incident can still see what happened.

The writer is intentionally dependency-free and best-effort — if the
destination directory cannot be created, we log a warning and move on rather
than letting a logging failure mask the original failure.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/var/log/eagle_point/critical_failures.jsonl"
_PATH_OVERRIDE_ENV = "EMERGENCY_LOG_PATH"

# Writing is serialized so that concurrent handlers don't interleave lines.
_write_lock = threading.Lock()


def _resolve_path() -> str:
    return os.environ.get(_PATH_OVERRIDE_ENV, _DEFAULT_PATH)


def record_critical_failure(
    category: str,
    message: str,
    **context: Any,
) -> None:
    """Append a JSON record describing a suppressed critical failure.

    Parameters
    ----------
    category:
        Short stable identifier for the failing subsystem, e.g.
        ``"audit_middleware"``, ``"alert_emission"``,
        ``"validation_persistence"``, ``"pii_decryption"``.
    message:
        Human-readable summary of what failed.
    **context:
        Any extra JSON-serializable fields. Values that are not serializable
        are converted via ``str(...)`` so the write never raises.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "message": message,
    }
    for key, value in context.items():
        try:
            json.dumps(value)
            record[key] = value
        except (TypeError, ValueError):
            record[key] = str(value)

    path = _resolve_path()
    try:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        with _write_lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Do NOT raise — the caller is already in a failure-handling branch.
        # A stderr warning is the fallback of the fallback.
        logger.warning(
            "emergency log write failed at %s: %s", path, exc, exc_info=False
        )
