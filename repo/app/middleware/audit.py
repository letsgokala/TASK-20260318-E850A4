"""Audit middleware — transactional fail-closed auditing.

The audit report flagged the previous design as a compliance gap: the
middleware wrote audit rows AFTER the handler had already committed, so a
domain write could persist even when the client received a 500.

This rewrite fixes that by tying the audit row into the **same transaction**
as the domain write:

1. The middleware extracts the request context (user, method, path,
   resource, client) on the inbound leg and stores it in a ContextVar.
2. A SQLAlchemy ``before_commit`` event listener, registered once at import
   time on ``sqlalchemy.orm.Session``, reads the ContextVar and — for
   mutating requests — stages an ``AuditLog`` row on the session before the
   commit flushes. The audit row is part of the same unit of work as the
   domain write.
3. If the commit fails (for any reason, including an audit-row constraint
   failure), the whole transaction is rolled back. The domain write does
   not persist and the client sees a 5xx. This is the fail-closed guarantee
   the README/design doc claimed but did not deliver.

A mutating request that errors out **before** any commit (e.g. a validation
rejection that returns 422) has no state change to protect, so it is
audited on a best-effort post-response write — that leg may fail open
without undermining the transactional guarantee for committed writes.

The ``AUDIT_FAIL_CLOSED`` env flag now controls the fail-mode for the
**best-effort post-response leg** only:

- ``AUDIT_FAIL_CLOSED="1"`` (default): a post-response audit failure is
  surfaced via the ``X-Audit-Log-Fallback`` header (the request already
  succeeded or failed without a domain write, so there is nothing to roll
  back). The transactional leg is always fail-closed.
- ``AUDIT_FAIL_CLOSED="0"``: the transactional event hook also skips,
  restoring the prior post-commit best-effort write for operators who
  accept the weaker guarantee.
"""
import logging
import uuid
from contextvars import ContextVar
from typing import Any, Optional

from sqlalchemy import event
from sqlalchemy.orm import Session as SyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.auth.jwt import decode_access_token
from app.database import async_session
from app.models.audit_log import AuditLog
from app.utils.emergency_log import record_critical_failure

logger = logging.getLogger(__name__)

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Per-request audit context. The middleware sets this on the inbound leg and
# the before_commit hook reads it. Using a ContextVar means the hook sees the
# correct request context even under concurrent async request handling.
_audit_context: ContextVar[Optional[dict]] = ContextVar(
    "eagle_point_audit_context", default=None
)


def current_audit_context() -> Optional[dict]:
    """Expose the live audit context to read-path handlers (e.g. downloads).

    Returns ``None`` outside of an HTTP request — e.g. during background
    tasks, migrations, or seed scripts — so those paths never get an
    accidental audit row.
    """
    return _audit_context.get()


# ── Session event: stage audit row in the same transaction ─────────────────

@event.listens_for(SyncSession, "before_commit")
def _audit_before_commit(session: SyncSession) -> None:
    """Stage an ``AuditLog`` row on the session for any committed mutation.

    Runs synchronously on the underlying Session used by AsyncSession. If
    adding the row fails — or if the flush/commit of the row fails — the
    transaction is rolled back as a whole, which is the fail-closed
    guarantee the design doc claims.
    """
    ctx = _audit_context.get()
    if not ctx or not ctx.get("is_mutating") or ctx.get("audit_written"):
        return

    # Allow opt-out for fail-open deployments. When fail-open is selected
    # the best-effort post-response write in the middleware takes over.
    from app.config import settings as _settings
    if _settings.AUDIT_FAIL_CLOSED != "1":
        return

    action = f"{ctx['method']} {ctx['path']}"
    if len(action) > 255:
        action = action[:255]

    log = AuditLog(
        user_id=ctx["user_id"],
        action=action,
        resource_type=ctx["resource_type"],
        resource_id=ctx["resource_id"],
        details={"transactional": True},
        ip_address=ctx["ip"],
        user_agent=ctx["user_agent"],
    )
    session.add(log)
    # Mark the context so a subsequent commit in the same request (rare, but
    # possible e.g. in batch flows that commit-per-item) does not duplicate
    # the row. The flag is per-request; it is reset when the ContextVar is
    # reset at end of dispatch.
    ctx["audit_written"] = True


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        mutating = (
            request.method in _MUTATING_METHODS
            # Login handles its own audit rows (success and each failure mode).
            and not request.url.path.endswith("/auth/login")
        )

        ctx: Optional[dict[str, Any]] = None
        if mutating:
            path_parts = request.url.path.strip("/").split("/")
            ctx = {
                "is_mutating": True,
                "user_id": self._extract_user_id(request),
                "method": request.method,
                "path": request.url.path,
                "ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
                "resource_type": path_parts[2] if len(path_parts) > 2 else None,
                "resource_id": (
                    self._try_uuid(path_parts[3]) if len(path_parts) > 3 else None
                ),
                "audit_written": False,
            }

        token = _audit_context.set(ctx)
        try:
            response = await call_next(request)
        finally:
            _audit_context.reset(token)

        # Propagate the read-audit fallback flag from request.state into
        # a real response header. The ``audit_read`` dependency sets
        # ``request.state.audit_read_fallback`` when it could not
        # persist the sensitive-read audit row; without this hop the
        # flag never reached the client, so operators/proxies had no
        # way to tell a read had been served without an audit trail.
        read_fallback = getattr(request.state, "audit_read_fallback", None)
        if read_fallback:
            response.headers["X-Audit-Log-Fallback"] = read_fallback

        if not mutating or ctx is None:
            return response

        # Transactional audit already fired during commit — nothing more to do.
        if ctx.get("audit_written"):
            return response

        # No commit happened (the handler raised before commit, or did not
        # mutate state). There is no domain write to protect, so we write a
        # best-effort attempted-action audit row on a fresh session so that
        # every mutating attempt still has a trail. This leg obeys
        # AUDIT_FAIL_CLOSED for backwards-compatible operator semantics.
        await self._write_attempted_audit(ctx, response)
        return response

    @staticmethod
    async def _write_attempted_audit(ctx: dict, response: Response) -> None:
        action = f"{ctx['method']} {ctx['path']}"
        if len(action) > 255:
            action = action[:255]
        try:
            async with async_session() as session:
                session.add(AuditLog(
                    user_id=ctx["user_id"],
                    action=action,
                    resource_type=ctx["resource_type"],
                    resource_id=ctx["resource_id"],
                    details={
                        "transactional": False,
                        "status_code": response.status_code,
                    },
                    ip_address=ctx["ip"],
                    user_agent=ctx["user_agent"],
                ))
                await session.commit()
        except Exception as exc:
            logger.error(
                "AuditMiddleware failed to persist attempted-action audit row",
                extra={
                    "action": action,
                    "user_id": str(ctx["user_id"]) if ctx.get("user_id") else None,
                    "status_code": response.status_code,
                },
                exc_info=True,
            )
            record_critical_failure(
                category="audit_middleware",
                message="failed to persist attempted-action audit row",
                action=action,
                user_id=str(ctx["user_id"]) if ctx.get("user_id") else None,
                resource_type=ctx["resource_type"],
                resource_id=str(ctx["resource_id"]) if ctx.get("resource_id") else None,
                status_code=response.status_code,
                error=repr(exc),
            )
            # This leg protects *attempted* (non-committed) mutations only;
            # there is no domain write to roll back. Surface the degraded
            # state via the fallback header so callers/proxies can tell.
            response.headers["X-Audit-Log-Fallback"] = "emergency-log"

    @staticmethod
    def _extract_user_id(request: Request) -> uuid.UUID | None:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload and "sub" in payload:
            try:
                return uuid.UUID(payload["sub"])
            except ValueError:
                return None
        return None

    @staticmethod
    def _try_uuid(value: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
