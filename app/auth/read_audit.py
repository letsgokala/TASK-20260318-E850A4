"""Sensitive-read audit helper.

The first audit report established transactional audit for mutating
requests (writes roll back atomically if the audit row cannot be
persisted). A later audit flagged a second gap: ordinary GET requests
for sensitive resources (registrations, materials lists, finance data,
notifications, admin audit-log views) are not audited at all.

This module exposes a FastAPI dependency factory that records a
``SENSITIVE_READ`` ``AuditLog`` row for the current request before the
handler returns. Unlike the mutating-write path, read audits:

* are **best-effort** — a failure to persist the audit row does not roll
  back the read (there is no domain mutation to protect), but it is
  logged at ERROR level and mirrored to the emergency JSONL sink via
  ``record_critical_failure``. Under ``AUDIT_FAIL_CLOSED=1`` the
  response carries an ``X-Audit-Log-Fallback: emergency-log`` header
  (the failure is surfaced but the user still sees the data — matching
  the pre-existing fail-open semantics for sensitive downloads when
  ``AUDIT_FAIL_CLOSED=0``).

Usage::

    from app.auth.read_audit import audit_read

    @router.get("/something", ...)
    async def something(
        request: Request,
        current_user: User = Depends(get_current_user),
        _audit: None = Depends(audit_read("something", "list")),
    ):
        ...

Applied to the sensitive-read surface so the prompt-level "access
auditing" requirement is enforced for reads, not only for mutations and
downloads.
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.utils.emergency_log import record_critical_failure

logger = logging.getLogger(__name__)


def _extract_resource_id(request: Request, resource_id_key: Optional[str]) -> Optional[uuid.UUID]:
    """Pull a resource id out of the path params if the caller named one."""
    if not resource_id_key:
        return None
    raw = request.path_params.get(resource_id_key)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def audit_read(
    resource_type: str,
    action_suffix: str = "read",
    resource_id_key: Optional[str] = None,
) -> Callable:
    """Return a FastAPI dependency that audits a sensitive read.

    Args:
        resource_type: short resource name recorded on the audit row
            (e.g. ``registration``, ``material``, ``finance_account``).
        action_suffix: human-readable action label appended to the
            request path (``read``, ``list``, ``view``).
        resource_id_key: optional path-parameter name to extract into
            ``audit_log.resource_id`` (e.g. ``"registration_id"``).
    """
    async def _dep(
        request: Request,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ) -> None:
        action = f"READ_{action_suffix} {request.method} {request.url.path}"
        if len(action) > 255:
            action = action[:255]
        resource_id = _extract_resource_id(request, resource_id_key)

        entry = AuditLog(
            user_id=current_user.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details={
                "transactional": False,
                "kind": "sensitive_read",
                "query": (
                    str(request.url.query) if request.url.query else None
                ),
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        db.add(entry)
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Sensitive-read audit write failed",
                extra={
                    "resource_type": resource_type,
                    "user_id": str(current_user.id),
                    "path": request.url.path,
                },
                exc_info=True,
            )
            record_critical_failure(
                category="audit_sensitive_read",
                message="failed to persist sensitive-read audit row",
                action=action,
                user_id=str(current_user.id),
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id else None,
                error=repr(exc),
            )
            # Best-effort contract: the read can still proceed because
            # there is no mutation at risk, but the degraded state is
            # surfaced to the caller via a response header. The
            # ``AuditMiddleware`` dispatch copies this flag into
            # ``X-Audit-Log-Fallback`` on the outgoing response, so the
            # client / reverse-proxy sees the same signal used on the
            # write-side fail-open path.
            request.state.audit_read_fallback = "emergency-log"

    return _dep
