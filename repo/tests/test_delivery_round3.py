"""Round-3 regression tests covering the last three Blocker/High audit findings.

Issues addressed:

1. Sensitive GETs were not audited. The ``audit_read`` dependency is
   applied to registration/material/finance/notification/admin read
   endpoints; these tests assert an ``AuditLog`` row appears after a
   GET.
2. Invoice attachments were write-only. A new
   ``GET /api/v1/finance/transactions/{id}/invoice`` endpoint streams
   the file under the same transactional-audit contract as material
   downloads; these tests assert the audit row and access controls.
3. Backup/restore omitted invoices. The restore handler now handles
   ``materials/`` and ``invoices/`` subdirs with per-subsystem exit
   codes; tests pin that ``complete`` requires *all* subsystems ok.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.collection_batch import CollectionBatch
from app.models.financial import FinancialTransaction, FundingAccount, TransactionType
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


# ── Helpers ────────────────────────────────────────────────────────────────

async def _funding_account_and_txn(
    db_session, admin_user, finance_user, *, with_invoice_path: bool = False,
    storage_root: str = "/tmp/invoice-test",
) -> tuple[FundingAccount, FinancialTransaction, str]:
    batch = CollectionBatch(
        name="Round3 Fin",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="R3 Fin",
        activity_type="test",
        description="desc",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()

    acct = FundingAccount(
        registration_id=reg.id,
        name="Round3 Account",
        allocated_budget=Decimal("1000.00"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.flush()

    txn = FinancialTransaction(
        funding_account_id=acct.id,
        type=TransactionType.EXPENSE,
        amount=Decimal("100.00"),
        category="travel",
        description="R3",
        recorded_by=finance_user.id,
    )
    db_session.add(txn)
    await db_session.commit()
    await db_session.refresh(txn)

    path = ""
    if with_invoice_path:
        os.makedirs(storage_root, exist_ok=True)
        path = os.path.join(storage_root, f"{txn.id}.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4 fake invoice bytes")
        txn.invoice_attachment_path = path
        await db_session.commit()

    return acct, txn, path


async def _count_audit(db_session, *, action_contains: str | None = None) -> int:
    q = select(func.count()).select_from(AuditLog)
    if action_contains:
        q = q.where(AuditLog.action.ilike(f"%{action_contains}%"))
    return (await db_session.execute(q)).scalar_one()


# ── High #1: Read-audit for sensitive GETs ────────────────────────────────

@pytest.mark.asyncio
async def test_registration_list_is_audited(
    client: AsyncClient, applicant_user, db_session,
):
    """GET /registrations must create a SENSITIVE_READ audit row."""
    before = await _count_audit(db_session, action_contains="READ_list")
    resp = await client.get(
        "/api/v1/registrations", headers=make_token(applicant_user)
    )
    assert resp.status_code == 200
    after = await _count_audit(db_session, action_contains="READ_list")
    assert after >= before + 1, (
        "GET /registrations must emit at least one sensitive-read audit row"
    )


@pytest.mark.asyncio
async def test_registration_detail_is_audited(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """GET /registrations/{id} must write an audit row referencing the
    resource type and id."""
    batch = CollectionBatch(
        name="Round3",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Audit me",
        activity_type="research",
        description="desc",
        applicant_name="Me",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)

    resp = await client.get(
        f"/api/v1/registrations/{reg.id}",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200

    # Audit row must exist for this exact registration id
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "registration",
                AuditLog.resource_id == reg.id,
                AuditLog.action.ilike("READ_detail%"),
            )
        )
    ).scalars().all()
    assert rows, "registration detail read must produce an audit row"
    assert rows[0].user_id == applicant_user.id


@pytest.mark.asyncio
async def test_finance_statistics_read_is_audited(
    client: AsyncClient, finance_user, db_session,
):
    before = await _count_audit(db_session, action_contains="READ_aggregate")
    resp = await client.get(
        "/api/v1/finance/statistics", headers=make_token(finance_user)
    )
    assert resp.status_code == 200
    after = await _count_audit(db_session, action_contains="READ_aggregate")
    assert after >= before + 1


# ── High #2: Invoice retrieval and access control ────────────────────────

@pytest.mark.asyncio
async def test_invoice_download_happy_path(
    client: AsyncClient, finance_user, admin_user, db_session, tmp_path,
):
    """Finance admin can download an attached invoice and the download
    writes a transactional audit row."""
    from app.api.v1 import finance as finance_mod

    # Redirect invoice storage to tmp_path so the path-under-root check
    # accepts our fixture file.
    storage_root = str(tmp_path)
    _orig = finance_mod._INVOICE_STORAGE_ROOT
    finance_mod._INVOICE_STORAGE_ROOT = storage_root
    try:
        _, txn, path = await _funding_account_and_txn(
            db_session,
            admin_user,
            finance_user,
            with_invoice_path=True,
            storage_root=storage_root,
        )

        resp = await client.get(
            f"/api/v1/finance/transactions/{txn.id}/invoice",
            headers=make_token(finance_user),
        )
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF"), "invoice bytes must stream"

        # Transactional audit row for the download
        row = (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.resource_type == "invoice",
                    AuditLog.resource_id == txn.id,
                )
            )
        ).scalars().first()
        assert row is not None, "invoice download must write an audit row"
        assert row.user_id == finance_user.id
        assert "DOWNLOAD invoice" in row.action
    finally:
        finance_mod._INVOICE_STORAGE_ROOT = _orig


@pytest.mark.asyncio
async def test_invoice_download_requires_finance_role(
    client: AsyncClient, applicant_user, admin_user, finance_user, db_session, tmp_path,
):
    """Applicants must not be able to fetch invoices even if they know
    the transaction id."""
    from app.api.v1 import finance as finance_mod
    _orig = finance_mod._INVOICE_STORAGE_ROOT
    finance_mod._INVOICE_STORAGE_ROOT = str(tmp_path)
    try:
        _, txn, _ = await _funding_account_and_txn(
            db_session, admin_user, finance_user,
            with_invoice_path=True, storage_root=str(tmp_path),
        )

        resp = await client.get(
            f"/api/v1/finance/transactions/{txn.id}/invoice",
            headers=make_token(applicant_user),
        )
        assert resp.status_code == 403
    finally:
        finance_mod._INVOICE_STORAGE_ROOT = _orig


@pytest.mark.asyncio
async def test_invoice_download_404_when_no_attachment(
    client: AsyncClient, finance_user, admin_user, db_session,
):
    """A transaction without an invoice must return 404, not 200."""
    _, txn, _ = await _funding_account_and_txn(
        db_session, admin_user, finance_user, with_invoice_path=False,
    )
    resp = await client.get(
        f"/api/v1/finance/transactions/{txn.id}/invoice",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


# ── High #3: Restore with invoice subsystem ──────────────────────────────

@pytest.mark.asyncio
async def test_restore_complete_requires_all_subsystems_ok(
    client: AsyncClient, admin_user, tmp_path, monkeypatch,
):
    """The new layout has materials/ and invoices/ subdirs. ``complete``
    status must require ALL three subsystems (db + materials + invoices)
    to succeed. If rsync fails on the invoices subdir only, the overall
    status must not be ``complete``."""
    date_str = "20250301"
    db_dump = tmp_path / f"backup_{date_str}.dump"
    db_dump.write_bytes(b"fake")
    date_dir = tmp_path / date_str
    (date_dir / "materials").mkdir(parents=True)
    (date_dir / "invoices").mkdir(parents=True)
    (date_dir / "materials" / "x").write_text("m")
    (date_dir / "invoices" / "y").write_text("i")

    from app.api.v1 import admin_ops as ops
    monkeypatch.setattr(ops, "_BACKUP_DB_DIR", str(tmp_path))
    monkeypatch.setattr(ops, "_BACKUP_FILES_DIR", str(tmp_path))

    def fake_run(cmd, *args, **kwargs):
        mock = MagicMock()
        mock.stderr = b""
        if cmd[0] == "pg_restore":
            mock.returncode = 0
        elif cmd[0] == "rsync":
            # Fail only the invoices subsystem
            source = cmd[-2]
            if "invoices" in source:
                mock.returncode = 23
                mock.stderr = b"rsync: invoices failed"
            else:
                mock.returncode = 0
        else:
            mock.returncode = 0
        return mock

    monkeypatch.setattr(ops.subprocess, "run", fake_run)

    resp = await client.post(
        f"/api/v1/admin/backups/{date_str}/restore",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] != "complete"
    assert body["invoices_exit_code"] == 23
    assert body["invoices_attempted"] is True
    assert body["files_exit_code"] == 0


# ── Static guards ─────────────────────────────────────────────────────────

def test_read_audit_dependency_applied_to_sensitive_paths():
    """Grep-level guard: every listed sensitive-read endpoint must include
    an ``audit_read`` dependency. Regressions here silently drop read
    auditing and reopen the gap the third audit called out."""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    paths = {
        "app/api/v1/registrations.py",
        "app/api/v1/materials.py",
        "app/api/v1/finance.py",
        "app/api/v1/metrics.py",
        "app/api/v1/admin_ops.py",
    }
    for rel in paths:
        text = (repo_root / rel).read_text()
        assert "from app.auth.read_audit import audit_read" in text, (
            f"{rel} missing audit_read import"
        )
        assert "audit_read(" in text, (
            f"{rel} does not wire audit_read anywhere"
        )


def test_backup_script_includes_invoices():
    """Structural guard on the backup helper — invoices must be backed up
    alongside materials so recovery does not lose finance evidence."""
    import pathlib
    script = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts" / "backup.sh"
    ).read_text()
    assert "/storage/invoices" in script, (
        "backup.sh must include /storage/invoices in the rsync set"
    )
    assert "INVOICES_BACKUP_DIR" in script, (
        "backup.sh must define a dedicated invoices backup directory"
    )


def test_finance_registers_invoice_download_route():
    """Static: the GET invoice route must exist on the app."""
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/finance/transactions/{transaction_id}/invoice" in paths
