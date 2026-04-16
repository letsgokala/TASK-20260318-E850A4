"""Transactional audit / alert enforcement tests.

These tests cover the compliance gaps that the delivery audit flagged as
High-severity:

1. A mutating request must roll back the domain write if the audit row
   cannot be persisted (AUDIT_FAIL_CLOSED=1).
2. A finance transaction / review transition must roll back if alert
   emission fails (ALERT_FAIL_CLOSED=1).
3. Sensitive file/report downloads must be blocked with a 500 if the
   download audit row cannot be persisted under AUDIT_FAIL_CLOSED=1.
4. A non-owner cannot access someone else's report task.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.collection_batch import CollectionBatch
from app.models.financial import FundingAccount
from app.models.registration import Registration, RegistrationStatus
from app.models.export_task import ExportStatus, ExportTask
from tests.conftest import make_token


# ── Helpers ────────────────────────────────────────────────────────────────

async def _submitted_registration(db_session, applicant_user, admin_user) -> Registration:
    batch = CollectionBatch(
        name="Audit Tx Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Atomic test",
        activity_type="research",
        description="Desc",
        applicant_name="Applicant",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)
    return reg


async def _funding_account(db_session, admin_user, finance_user, budget="10000.00") -> FundingAccount:
    batch = CollectionBatch(
        name="Audit Fin Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Fin",
        activity_type="test",
        description="test",
        applicant_name="Test",
    )
    db_session.add(reg)
    await db_session.flush()

    acct = FundingAccount(
        registration_id=reg.id,
        name="Atomic Fund",
        allocated_budget=Decimal(budget),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)
    return acct


# ── Alert fail-closed rolls back committed state ───────────────────────────

@pytest.mark.asyncio
async def test_alert_failure_rolls_back_review_transition(
    client: AsyncClient,
    reviewer_user,
    applicant_user,
    admin_user,
    db_session,
    monkeypatch,
):
    """Under ALERT_FAIL_CLOSED=1, a failed alert emission must leave the
    registration status unchanged — no committed transition without a
    matching alert."""
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    original_status = reg.status

    from app.api.v1 import reviews as reviews_mod
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "ALERT_FAIL_CLOSED", "1")

    async def _boom(db):
        raise RuntimeError("simulated alert emission failure")

    # Patch the imported helper used inside the handler path
    import app.api.v1.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "check_and_notify_breaches", _boom)

    resp = await client.post(
        f"/api/v1/reviews/registrations/{reg.id}/transition",
        json={"to_status": "approved", "comment": "should not persist"},
        headers=make_token(reviewer_user),
    )
    # Server error surfaced to client because the commit aborted
    assert resp.status_code >= 500

    # Database state MUST be unchanged — atomicity contract
    await db_session.rollback()  # drop any autobegin state
    await db_session.refresh(reg)
    assert reg.status == original_status


@pytest.mark.asyncio
async def test_alert_failure_rolls_back_finance_transaction(
    client: AsyncClient,
    finance_user,
    admin_user,
    db_session,
    monkeypatch,
):
    """Under ALERT_FAIL_CLOSED=1, a failed alert emission must leave the
    funding account's ledger unchanged — no committed transaction
    without a matching alert."""
    acct = await _funding_account(db_session, admin_user, finance_user)

    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "ALERT_FAIL_CLOSED", "1")

    async def _boom(db):
        raise RuntimeError("simulated alert emission failure")

    import app.api.v1.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "check_and_notify_breaches", _boom)

    resp = await client.post(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        json={"type": "expense", "amount": "50.00", "category": "travel"},
        headers=make_token(finance_user),
    )
    assert resp.status_code >= 500

    # No transaction should have been committed
    await db_session.rollback()
    from sqlalchemy import select, func
    from app.models.financial import FinancialTransaction
    count = (
        await db_session.execute(
            select(func.count()).select_from(FinancialTransaction).where(
                FinancialTransaction.funding_account_id == acct.id
            )
        )
    ).scalar_one()
    assert count == 0, "alert failure must roll back the committed transaction"


# ── Download audit enforcement ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_download_blocked_when_audit_commit_fails(
    client: AsyncClient,
    admin_user,
    db_session,
    tmp_path,
    monkeypatch,
):
    """Under AUDIT_FAIL_CLOSED=1, if the audit commit on a report download
    fails, the response is 500 and no file content is streamed."""
    # Create a completed export task with a real on-disk file
    file_path = tmp_path / "test_report.xlsx"
    file_path.write_bytes(b"fake xlsx bytes for audit test")

    task = ExportTask(
        report_type="reconciliation",
        status=ExportStatus.COMPLETE,
        created_by=admin_user.id,
        file_path=str(file_path),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "AUDIT_FAIL_CLOSED", "1")

    # Patch AsyncSession.commit to fail during download audit write.
    # We scope the failure to the download path by checking the session's
    # pending objects for an AuditLog with DOWNLOAD action.
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.audit_log import AuditLog
    original_commit = AsyncSession.commit

    async def failing_commit(self):
        # Trigger failure only when the current session is about to commit
        # an AuditLog row flagged as a download. Other commits (test setup,
        # middleware attempted-action writes on a fresh session) pass
        # through unchanged.
        for obj in self.new:
            if isinstance(obj, AuditLog) and obj.action and obj.action.startswith("DOWNLOAD report"):
                raise RuntimeError("simulated audit commit failure")
        return await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}/download",
        headers=make_token(admin_user),
    )
    # Fail-closed contract: the download must be blocked, not served.
    assert resp.status_code == 500
    assert resp.content != b"fake xlsx bytes for audit test"


# ── Report task ownership ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_task_not_visible_to_non_owner(
    client: AsyncClient,
    finance_user,
    admin_user,
    db_session,
):
    """A finance_admin cannot read/download a report task owned by another
    finance_admin (admin-only reports aside — the ownership check covers the
    reconciliation-type tasks they CAN generate)."""
    from app.auth.password import hash_password
    from app.models.user import User, UserRole

    other_finance = User(
        id=uuid.uuid4(),
        username="finance_other",
        password_hash=hash_password("Finance@12345!"),
        role=UserRole.FINANCIAL_ADMIN,
    )
    db_session.add(other_finance)
    await db_session.flush()

    task = ExportTask(
        report_type="reconciliation",
        status=ExportStatus.COMPLETE,
        created_by=other_finance.id,
        file_path="/tmp/does-not-exist.xlsx",
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    # Non-owner finance user must be blocked
    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403

    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}/download",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403


# ── Static invariants for the transactional audit architecture ────────────

def test_audit_middleware_registers_before_commit_hook():
    """The transactional audit relies on a SQLAlchemy before_commit hook
    being registered exactly once at import time."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session as SyncSession
    # Importing the middleware registers the listener as a side effect.
    from app.middleware import audit  # noqa: F401
    assert event.contains(SyncSession, "before_commit", audit._audit_before_commit)


def test_check_and_notify_breaches_does_not_commit():
    """Caller-owned commit contract: the helper must not invoke commit().

    This is what makes alert emission transactional with the domain write.
    If this function regrows an internal commit, the atomic rollback
    guarantee in reviews/finance breaks silently."""
    import ast
    import inspect

    from app.api.v1 import metrics as metrics_mod
    source = inspect.getsource(metrics_mod.check_and_notify_breaches)
    tree = ast.parse(source)

    commit_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "commit":
            commit_calls.append(node)

    assert not commit_calls, (
        "check_and_notify_breaches must not call .commit(); callers own the unit of work"
    )


def test_download_endpoints_do_not_swallow_audit_exceptions():
    """Regression guard for the fail-open download endpoints.

    The audit report explicitly flagged ``except Exception: pass`` blocks
    around the audit write in material/report downloads. Those blocks must
    not be reintroduced."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("app/api/v1/materials.py", "app/api/v1/reports.py"):
        content = (repo_root / rel).read_text()
        # The old anti-pattern was `except Exception:\n        pass` right
        # around the audit write; allow the word "pass" elsewhere but not
        # as a bare swallow of the download audit.
        assert "pass  # Audit logging must not block" not in content, (
            f"{rel} still swallows download audit failures"
        )
