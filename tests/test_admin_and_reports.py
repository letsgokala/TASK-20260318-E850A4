"""Tests for admin ops authorization, report export access control,
and duplicate-check endpoint scoping."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist_item import ChecklistItem
from app.models.collection_batch import CollectionBatch
from app.models.export_task import ExportStatus, ExportTask
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


# ── Admin-ops authorization ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backup_list_requires_system_admin(client: AsyncClient, applicant_user, reviewer_user, finance_user):
    """Only system_admin may list backups; all other roles must get 403."""
    for user in (applicant_user, reviewer_user, finance_user):
        resp = await client.get("/api/v1/admin/backups", headers=make_token(user))
        assert resp.status_code == 403, f"Expected 403 for role {user.role}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_audit_log_requires_system_admin(client: AsyncClient, applicant_user, reviewer_user, finance_user):
    """Only system_admin may read audit logs."""
    for user in (applicant_user, reviewer_user, finance_user):
        resp = await client.get("/api/v1/admin/audit-logs", headers=make_token(user))
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_integrity_check_requires_system_admin(client: AsyncClient, applicant_user):
    """Integrity check is admin-only."""
    resp = await client.post("/api/v1/admin/integrity-check", headers=make_token(applicant_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_user_list_requires_system_admin(client: AsyncClient, reviewer_user, finance_user):
    """User listing endpoint is admin-only."""
    for user in (reviewer_user, finance_user):
        resp = await client.get("/api/v1/admin/users", headers=make_token(user))
        assert resp.status_code == 403


# ── Report export access control ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_generation_requires_finance_or_admin(
    client: AsyncClient, applicant_user, reviewer_user
):
    """Applicants and reviewers must not be able to generate reports."""
    for user in (applicant_user, reviewer_user):
        resp = await client.post("/api/v1/reports/generate/reconciliation", headers=make_token(user))
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_report_task_poll_scoped_to_creator(
    client: AsyncClient, finance_user, admin_user, db_session: AsyncSession
):
    """A finance user must not access another user's export task."""
    task = ExportTask(
        id=uuid.uuid4(),
        report_type="audit",
        status=ExportStatus.COMPLETE,
        created_by=admin_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    headers = make_token(finance_user)
    resp = await client.get(f"/api/v1/reports/tasks/{task.id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_report_response_does_not_expose_file_path(
    client: AsyncClient, admin_user, db_session: AsyncSession
):
    """ExportTaskResponse must not include the raw file_path field."""
    task = ExportTask(
        id=uuid.uuid4(),
        report_type="whitelist",
        status=ExportStatus.COMPLETE,
        file_path="/internal/reports/whitelist_xyz.xlsx",
        created_by=admin_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    headers = make_token(admin_user)
    resp = await client.get(f"/api/v1/reports/tasks/{task.id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "file_path" not in data, "Internal file_path must not appear in response"


# ── Duplicate-check scoping ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_check_requires_reviewer_or_admin(
    client: AsyncClient, applicant_user, finance_user
):
    """Applicants and finance users must be blocked from the duplicate endpoint."""
    fake_hash = "a" * 64
    for user in (applicant_user, finance_user):
        resp = await client.get(
            f"/api/v1/materials/duplicates?hash={fake_hash}",
            headers=make_token(user),
        )
        assert resp.status_code == 403, f"Expected 403 for {user.role}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_duplicate_check_does_not_return_draft_matches(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session: AsyncSession
):
    """Draft-registration materials must not appear in duplicate lookup results."""
    batch = CollectionBatch(
        name="Dup Test Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    # Draft registration with a material version
    draft_reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Draft",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(draft_reg)
    await db_session.flush()

    # PG enforces FK on checklist_item_id — the SQLite lane tolerated a
    # dangling UUID here, PG does not. Create the referenced row explicitly.
    checklist_item = ChecklistItem(batch_id=batch.id, label="Dup item")
    db_session.add(checklist_item)
    await db_session.flush()

    material = Material(registration_id=draft_reg.id, checklist_item_id=checklist_item.id)
    db_session.add(material)
    await db_session.flush()

    test_hash = "b" * 64
    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        original_filename="secret.pdf",
        mime_type="application/pdf",
        file_size_bytes=512,
        sha256_hash=test_hash,
        storage_path="/tmp/secret.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(version)
    await db_session.commit()

    headers = make_token(reviewer_user)
    resp = await client.get(
        f"/api/v1/materials/duplicates?hash={test_hash}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == [], "Draft registration materials must not be returned by duplicate check"


# ── Finance response schema ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transaction_response_has_no_invoice_path(
    client: AsyncClient, finance_user, admin_user, db_session: AsyncSession
):
    """TransactionResponse must expose has_invoice bool, not the raw invoice_attachment_path."""
    from decimal import Decimal
    from app.models.financial import FundingAccount, FinancialTransaction, TransactionType

    batch = CollectionBatch(
        name="Finance Schema Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="T",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()

    acct = FundingAccount(
        registration_id=reg.id,
        name="Test",
        allocated_budget=Decimal("5000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    headers = make_token(finance_user)
    resp = await client.post(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        json={"type": "expense", "amount": "100.00", "category": "test"},
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "has_invoice" in data, "TransactionResponse must include has_invoice"
    assert data["has_invoice"] is False
    assert "invoice_attachment_path" not in data, "Raw file path must not be in response"


# ── Route resolution: /reports/tasks must not be shadowed ─────────────────

@pytest.mark.asyncio
async def test_reports_tasks_route_not_shadowed(
    client: AsyncClient, finance_user, db_session: AsyncSession
):
    """`GET /api/v1/reports/tasks` must return a list, not a 400 'invalid report type' error.

    Before the fix, the dynamic `/{report_type}` route captured 'tasks' and
    returned HTTP 400. After the fix (route renamed to /generate/{report_type}),
    /tasks resolves to list_export_tasks and returns HTTP 200 with a JSON list.
    """
    headers = make_token(finance_user)
    resp = await client.get("/api/v1/reports/tasks", headers=headers)
    assert resp.status_code == 200, (
        f"Expected 200 from /reports/tasks but got {resp.status_code}. "
        "Route may still be shadowed by /{report_type}."
    )
    assert isinstance(resp.json(), list), "/reports/tasks must return a JSON array"


@pytest.mark.asyncio
async def test_report_generate_route_uses_generate_prefix(
    client: AsyncClient, finance_user
):
    """`POST /api/v1/reports/generate/<type>` must be the generation endpoint.

    Report generation creates an ExportTask row and a file on disk, so it uses
    POST rather than GET. Verifying the route returns 400 (not 404) on an
    invalid type confirms the route is properly registered.
    """
    headers = make_token(finance_user)
    # The endpoint requires a valid report type; 'invalid_type' triggers a 400
    # from the business-logic guard (not a routing 404).
    resp = await client.post("/api/v1/reports/generate/invalid_type", headers=headers)
    assert resp.status_code == 400, (
        f"Expected 400 (invalid type) from /generate/invalid_type, got {resp.status_code}. "
        "If 404, the /generate/ route is not registered."
    )


# ── Admin success paths ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backup_list_accessible_to_system_admin(client: AsyncClient, admin_user):
    """System admin can reach the backup list endpoint (may return empty list)."""
    resp = await client.get("/api/v1/admin/backups", headers=make_token(admin_user))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_audit_log_accessible_to_system_admin(client: AsyncClient, admin_user):
    """System admin can read audit logs (may return empty list)."""
    resp = await client.get("/api/v1/admin/audit-logs", headers=make_token(admin_user))
    assert resp.status_code == 200
