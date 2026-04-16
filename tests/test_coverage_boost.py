"""Targeted tests to push total coverage past 90%.

Covers the following previously-untested paths:
  - reports/generator.py  (generate_reconciliation/audit/compliance/whitelist)
  - api/v1/reports.py     (sync report generation, download success path)
  - api/v1/materials.py   (create material, upload version, list, download)
  - api/v1/finance.py     (create account, list accounts, list transactions,
                           invoice upload)
  - api/v1/admin_ops.py   (integrity check, restore bad date, restore missing)
  - api/v1/registrations.py (create, update draft, submit)
"""
import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.collection_batch import CollectionBatch
from app.models.checklist_item import ChecklistItem
from app.models.financial import FundingAccount, FinancialTransaction, TransactionType
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.auth.password import hash_password
from tests.conftest import make_token


# ── Shared helpers ─────────────────────────────────────────────────────────

async def _make_batch(db_session, admin_user, days_ahead=30):
    batch = CollectionBatch(
        name=f"Boost Batch {uuid.uuid4().hex[:6]}",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=days_ahead),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    return batch


async def _make_submitted_reg(db_session, applicant_user, admin_user, batch=None):
    if batch is None:
        batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Boost Test",
        activity_type="conference",
        description="Test registration",
        applicant_name="Boost Tester",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)
    return reg, batch


async def _make_checklist_item(db_session, batch):
    item = ChecklistItem(batch_id=batch.id, label="Boost Doc", is_required=True)
    db_session.add(item)
    await db_session.flush()
    return item


async def _make_material(db_session, reg, item):
    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.flush()
    return mat


# ═══════════════════════════════════════════════════════════════════════════
# Registration create / update / submit
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_registration_via_api(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/registrations",
        json={
            "batch_id": str(batch.id),
            "title": "API Created Registration",
            "activity_type": "workshop",
            "description": "A workshop registration",
            "applicant_name": "API User",
            "applicant_id_number": "ID-001",
            "applicant_phone": "555-1234",
            "applicant_email": "api@example.com",
        },
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "API Created Registration"
    assert data["status"] == "draft"


@pytest.mark.asyncio
async def test_create_registration_missing_batch(client: AsyncClient, applicant_user):
    resp = await client.post(
        "/api/v1/registrations",
        json={
            "batch_id": str(uuid.uuid4()),
            "title": "No Batch",
            "activity_type": "workshop",
            "description": "desc",
            "applicant_name": "nobody",
        },
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_draft_registration(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Old Title",
        activity_type="seminar",
        description="Old desc",
        applicant_name="Old Name",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)

    resp = await client.put(
        f"/api/v1/registrations/{reg.id}/draft",
        json={"title": "New Title"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_submit_registration(client: AsyncClient, applicant_user, admin_user, db_session):
    # PII fields must be stored encrypted — the submit handler decrypts them
    # and (under DECRYPT_FAIL_CLOSED=1, now the default) a plaintext value
    # raises InvalidToken. Mirror the production write path by encrypting
    # before inserting.
    from app.utils.encryption import encrypt_value

    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Submit Me",
        activity_type="conference",
        description="Some description",
        applicant_name="Applicant A",
        applicant_id_number=encrypt_value("ID001"),
        applicant_phone=encrypt_value("555-0001"),
        applicant_email=encrypt_value("a@b.com"),
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/submit",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_get_registration_detail(client: AsyncClient, applicant_user, admin_user, db_session):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get(
        f"/api/v1/registrations/{reg.id}",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(reg.id)


# ═══════════════════════════════════════════════════════════════════════════
# Materials — create, upload, list, download
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_material_via_api(client: AsyncClient, applicant_user, admin_user, db_session):
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials?checklist_item_id={item.id}",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 201
    assert resp.json()["checklist_item_id"] == str(item.id)


@pytest.mark.asyncio
async def test_create_material_wrong_batch(client: AsyncClient, applicant_user, admin_user, db_session):
    """Checklist item from another batch should return 400."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    # Create item in a different batch
    other_batch = await _make_batch(db_session, admin_user)
    other_item = ChecklistItem(batch_id=other_batch.id, label="Other", is_required=False)
    db_session.add(other_item)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials?checklist_item_id={other_item.id}",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_material_duplicate_409(client: AsyncClient, applicant_user, admin_user, db_session):
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials?checklist_item_id={item.id}",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_upload_version_and_list_materials(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    import app.api.v1.materials as mat_module
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)
    await db_session.commit()

    headers = make_token(applicant_user)

    # Upload a version
    pdf_content = b"%PDF-1.4 " + b"x" * 512
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials/{mat.id}/versions",
        files={"file": ("doc.pdf", io.BytesIO(pdf_content), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 201
    ver_data = resp.json()
    assert ver_data["version_number"] == 1
    assert ver_data["mime_type"] == "application/pdf"

    # List materials for registration
    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/materials",
        headers=headers,
    )
    assert resp.status_code == 200
    materials = resp.json()
    assert len(materials) >= 1
    assert any(m["id"] == str(mat.id) for m in materials)


@pytest.mark.asyncio
async def test_download_material_version(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path
):
    """Seed a version with a real file on disk and verify download."""
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)

    pdf_content = b"%PDF-1.4 test content"
    fake_file = tmp_path / "test.pdf"
    fake_file.write_bytes(pdf_content)

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(pdf_content),
        sha256_hash=hashlib.sha256(pdf_content).hexdigest(),
        storage_path=str(fake_file),
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(ver)

    resp = await client.get(
        f"/api/v1/registrations/versions/{ver.id}/download",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_finance_admin_cannot_download_material(
    client: AsyncClient, finance_user, applicant_user, admin_user, db_session, tmp_path
):
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)

    fake_file = tmp_path / "fin.pdf"
    fake_file.write_bytes(b"%PDF test")

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="fin.pdf",
        mime_type="application/pdf",
        file_size_bytes=9,
        sha256_hash=hashlib.sha256(b"%PDF test").hexdigest(),
        storage_path=str(fake_file),
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(ver)

    resp = await client.get(
        f"/api/v1/registrations/versions/{ver.id}/download",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Finance — create account, list, transactions, invoice
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_funding_account_via_api(
    client: AsyncClient, finance_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, admin_user, admin_user)

    resp = await client.post(
        "/api/v1/finance/accounts",
        json={
            "registration_id": str(reg.id),
            "name": "API Account",
            "allocated_budget": "8000.00",
        },
        headers=make_token(finance_user),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "API Account"


@pytest.mark.asyncio
async def test_create_funding_account_missing_registration(
    client: AsyncClient, finance_user
):
    resp = await client.post(
        "/api/v1/finance/accounts",
        json={
            "registration_id": str(uuid.uuid4()),
            "name": "Ghost Account",
            "allocated_budget": "1000.00",
        },
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_funding_accounts(client: AsyncClient, finance_user, admin_user, db_session):
    reg, _ = await _make_submitted_reg(db_session, admin_user, admin_user)
    acct = FundingAccount(
        registration_id=reg.id,
        name="Listed Account",
        allocated_budget=Decimal("3000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()

    resp = await client.get("/api/v1/finance/accounts", headers=make_token(finance_user))
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_list_transactions_for_account(
    client: AsyncClient, finance_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, admin_user, admin_user)
    acct = FundingAccount(
        registration_id=reg.id,
        name="Txn List Acct",
        allocated_budget=Decimal("5000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.flush()

    txn = FinancialTransaction(
        funding_account_id=acct.id,
        type=TransactionType.EXPENSE,
        amount=Decimal("100"),
        category="supplies",
        recorded_by=finance_user.id,
    )
    db_session.add(txn)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_list_transactions_account_not_found(client: AsyncClient, finance_user):
    resp = await client.get(
        f"/api/v1/finance/accounts/{uuid.uuid4()}/transactions",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_invoice(
    client: AsyncClient, finance_user, admin_user, db_session, tmp_path, monkeypatch
):
    import app.api.v1.finance as fin_module
    monkeypatch.setattr(fin_module, "_INVOICE_STORAGE_ROOT", str(tmp_path))

    reg, _ = await _make_submitted_reg(db_session, admin_user, admin_user)
    acct = FundingAccount(
        registration_id=reg.id,
        name="Invoice Acct",
        allocated_budget=Decimal("2000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.flush()

    txn = FinancialTransaction(
        funding_account_id=acct.id,
        type=TransactionType.EXPENSE,
        amount=Decimal("200"),
        category="travel",
        recorded_by=finance_user.id,
    )
    db_session.add(txn)
    await db_session.commit()
    await db_session.refresh(txn)

    resp = await client.post(
        f"/api/v1/finance/transactions/{txn.id}/invoice",
        files={"file": ("receipt.pdf", io.BytesIO(b"%PDF-1.4 receipt"), "application/pdf")},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 200
    assert resp.json()["has_invoice"] is True


@pytest.mark.asyncio
async def test_upload_invoice_wrong_mime(client: AsyncClient, finance_user, admin_user, db_session):
    reg, _ = await _make_submitted_reg(db_session, admin_user, admin_user)
    acct = FundingAccount(
        registration_id=reg.id,
        name="Mime Test",
        allocated_budget=Decimal("1000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.flush()

    txn = FinancialTransaction(
        funding_account_id=acct.id,
        type=TransactionType.EXPENSE,
        amount=Decimal("50"),
        category="misc",
        recorded_by=finance_user.id,
    )
    db_session.add(txn)
    await db_session.commit()
    await db_session.refresh(txn)

    resp = await client.post(
        f"/api/v1/finance/transactions/{txn.id}/invoice",
        files={"file": ("bad.exe", io.BytesIO(b"MZ\x90"), "application/octet-stream")},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_invoice_transaction_not_found(client: AsyncClient, finance_user):
    resp = await client.post(
        f"/api/v1/finance/transactions/{uuid.uuid4()}/invoice",
        files={"file": ("r.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Admin ops — integrity check, restore
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integrity_check_no_versions(client: AsyncClient, admin_user):
    """Integrity check with no material versions returns ok status."""
    resp = await client.post(
        "/api/v1/admin/integrity-check",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["ok"] == 0


@pytest.mark.asyncio
async def test_integrity_check_with_matching_file(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path
):
    """File exists and hash matches → ok."""
    content = b"%PDF-1.4 integrity check test"
    fake_file = tmp_path / "check.pdf"
    fake_file.write_bytes(content)

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="check.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(content),
        sha256_hash=hashlib.sha256(content).hexdigest(),
        storage_path=str(fake_file),
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/admin/integrity-check",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["ok"] == 1
    assert data["missing_count"] == 0


@pytest.mark.asyncio
async def test_integrity_check_missing_file(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """File missing from disk → reported in missing list."""
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="gone.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        sha256_hash="a" * 64,
        storage_path="/nonexistent/path/gone.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/admin/integrity-check",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["missing"]) == 1


@pytest.mark.asyncio
async def test_restore_backup_invalid_date_format(client: AsyncClient, admin_user):
    resp = await client.post(
        "/api/v1/admin/backups/invalid-date/restore",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_restore_backup_missing_dump(client: AsyncClient, admin_user):
    resp = await client.post(
        "/api/v1/admin/backups/20200101/restore",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Reports — sync generation (reconciliation, audit, compliance, whitelist)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_reconciliation_report(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Reconciliation report generates an Excel file."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    resp = await client.post(
        "/api/v1/reports/generate/reconciliation",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_type"] == "reconciliation"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_audit_report(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    resp = await client.post(
        "/api/v1/reports/generate/audit",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_type"] == "audit"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_compliance_report(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    resp = await client.post(
        "/api/v1/reports/generate/compliance",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_type"] == "compliance"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_whitelist_report(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    resp = await client.post(
        "/api/v1/reports/generate/whitelist",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_type"] == "whitelist"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_download_generated_report(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Generate a report and then download it."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    # Generate a report first
    gen_resp = await client.post(
        "/api/v1/reports/generate/reconciliation",
        headers=make_token(admin_user),
    )
    assert gen_resp.status_code == 201
    task_id = gen_resp.json()["id"]
    task_status = gen_resp.json()["status"]

    if task_status == "complete":
        # Download it
        dl_resp = await client.get(
            f"/api/v1/reports/tasks/{task_id}/download",
            headers=make_token(admin_user),
        )
        assert dl_resp.status_code == 200


@pytest.mark.asyncio
async def test_generate_report_with_data(
    client: AsyncClient, admin_user, applicant_user, finance_user, db_session, tmp_path, monkeypatch
):
    """Generate a reconciliation report with actual data to exercise more generator lines."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # Add a funding account
    acct = FundingAccount(
        registration_id=reg.id,
        name="Data Account",
        allocated_budget=Decimal("5000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.flush()

    txn = FinancialTransaction(
        funding_account_id=acct.id,
        type=TransactionType.EXPENSE,
        amount=Decimal("1000"),
        category="equipment",
        recorded_by=finance_user.id,
    )
    db_session.add(txn)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/reports/generate/reconciliation",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_list_export_tasks(client: AsyncClient, admin_user):
    """Admin can list their own export tasks."""
    resp = await client.get("/api/v1/reports/tasks", headers=make_token(admin_user))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ═══════════════════════════════════════════════════════════════════════════
# Reviews — batch and history
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_batch_review_transition(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    """Batch transition endpoint processes multiple registrations."""
    reg1, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    reg2, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.post(
        "/api/v1/reviews/batch",
        json={
            "registration_ids": [str(reg1.id), str(reg2.id)],
            "action": "approved",
            "comment": "Batch approved",
        },
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data


@pytest.mark.asyncio
async def test_review_transition_not_found(client: AsyncClient, reviewer_user):
    resp = await client.post(
        f"/api/v1/reviews/registrations/{uuid.uuid4()}/transition",
        json={"to_status": "approved"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_waitlist(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.post(
        f"/api/v1/reviews/registrations/{reg.id}/transition",
        json={"to_status": "waitlisted", "comment": "Waitlisted"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 201
    assert resp.json()["to_status"] == "waitlisted"


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases and auth
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_locked_account_login(client: AsyncClient, admin_user, db_session):
    """Login with a locked account returns 423."""
    admin_user.locked_until = datetime.now(timezone.utc) + timedelta(hours=1)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Admin@12345678!",
    })
    assert resp.status_code == 423
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_maintenance_mode_check(client: AsyncClient):
    """Maintenance mode endpoint works (GET /health doesn't block)."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Reports generator — loop bodies (need data in DB during generation)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_audit_report_with_log_data(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Audit report loop body executes when AuditLog entries exist."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    from app.models.audit_log import AuditLog
    log = AuditLog(
        user_id=admin_user.id,
        action="COVERAGE_TEST_ACTION",
        resource_type="registration",
        resource_id=uuid.uuid4(),
        details={"key": "value"},
    )
    db_session.add(log)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/reports/generate/audit",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["report_type"] == "audit"
    assert data["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_audit_report_with_date_filters(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Audit report with from_date/to_date filters covers date filter lines."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    from app.models.audit_log import AuditLog
    log = AuditLog(user_id=admin_user.id, action="DATE_FILTER_TEST")
    db_session.add(log)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/reports/generate/audit?from_date=2020-01-01T00:00:00&to_date=2030-01-01T00:00:00",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_compliance_with_submitted_registrations(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path, monkeypatch
):
    """Compliance report loop body executes when submitted registrations exist."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # Also create a registration with supplementary_used=True to cover that branch
    batch2 = await _make_batch(db_session, admin_user)
    reg2 = Registration(
        batch_id=batch2.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Supp Used Reg",
        activity_type="conference",
        description="Test",
        applicant_name="Supp Tester",
        supplementary_used=True,
    )
    db_session.add(reg2)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/reports/generate/compliance",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_compliance_with_batch_filter(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path, monkeypatch
):
    """Compliance report with batch_id filter covers batch filter line."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.post(
        f"/api/v1/reports/generate/compliance?batch_id={batch.id}",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_compliance_with_date_filters(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path, monkeypatch
):
    """Compliance report with date filters covers from_date/to_date lines."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.post(
        "/api/v1/reports/generate/compliance?from_date=2020-01-01T00:00:00&to_date=2030-01-01T00:00:00",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_whitelist_with_batch_items(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Whitelist loop body executes when CollectionBatch with ChecklistItems exists."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    # Batch WITH checklist items (covers the for-item loop path)
    batch_with_items = await _make_batch(db_session, admin_user)
    item = await _make_checklist_item(db_session, batch_with_items)

    # Batch WITHOUT checklist items (covers the no-items branch)
    batch_empty = CollectionBatch(
        name=f"Empty Batch {uuid.uuid4().hex[:6]}",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=10),
        created_by=admin_user.id,
    )
    db_session.add(batch_empty)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/reports/generate/whitelist",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_whitelist_with_batch_id_filter(
    client: AsyncClient, admin_user, db_session, tmp_path, monkeypatch
):
    """Whitelist with batch_id filter covers batch filter line."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    batch = await _make_batch(db_session, admin_user)
    item = await _make_checklist_item(db_session, batch)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/reports/generate/whitelist?batch_id={batch.id}",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "complete"


@pytest.mark.asyncio
async def test_generate_reconciliation_with_filters(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path, monkeypatch
):
    """Reconciliation with batch_id + date filters covers those filter lines."""
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # batch_id filter (generator line 60)
    resp = await client.post(
        f"/api/v1/reports/generate/reconciliation?batch_id={batch.id}",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201

    # date filters (generator lines 62, 64)
    resp = await client.post(
        "/api/v1/reports/generate/reconciliation?from_date=2020-01-01T00:00:00&to_date=2030-01-01T00:00:00",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════
# Admin ops — extra coverage: audit log filters, hash mismatch, list backups
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_audit_logs_with_all_filters(
    client: AsyncClient, admin_user, db_session
):
    """Exercise all four audit log filter branches."""
    from app.models.audit_log import AuditLog
    log = AuditLog(
        user_id=admin_user.id,
        action="FILTER_ACTION",
        resource_type="filter_resource",
    )
    db_session.add(log)
    await db_session.commit()

    base = "/api/v1/admin/audit-logs"
    headers = make_token(admin_user)

    # user_id filter (line 231)
    resp = await client.get(f"{base}?user_id={admin_user.id}", headers=headers)
    assert resp.status_code == 200

    # action filter (line 235)
    resp = await client.get(f"{base}?action=FILTER_ACTION", headers=headers)
    assert resp.status_code == 200

    # resource_type filter (line 237)
    resp = await client.get(f"{base}?resource_type=filter_resource", headers=headers)
    assert resp.status_code == 200

    # from date filter (line 239) — alias "from"
    resp = await client.get(f"{base}?from=2020-01-01T00:00:00", headers=headers)
    assert resp.status_code == 200

    # to date filter (line 240) — alias "to"
    resp = await client.get(f"{base}?to=2030-01-01T00:00:00", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_integrity_check_hash_mismatch(
    client: AsyncClient, admin_user, applicant_user, db_session, tmp_path
):
    """File exists on disk but stored hash is wrong → reported in hash_mismatch."""
    content = b"original file content"
    fake_file = tmp_path / "mismatch.pdf"
    fake_file.write_bytes(content)

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = await _make_checklist_item(db_session, batch)
    mat = await _make_material(db_session, reg, item)

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="mismatch.pdf",
        mime_type="application/pdf",
        file_size_bytes=len(content),
        sha256_hash="0" * 64,  # intentionally wrong hash
        storage_path=str(fake_file),
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/admin/integrity-check",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mismatch_count"] == 1
    assert len(data["hash_mismatch"]) == 1


@pytest.mark.asyncio
async def test_list_backups_with_backup_dir(
    client: AsyncClient, admin_user, tmp_path, monkeypatch
):
    """When backup directory has dump files, they appear in the list."""
    import app.api.v1.admin_ops as admin_ops_module
    monkeypatch.setattr(admin_ops_module, "_BACKUP_DB_DIR", str(tmp_path))
    monkeypatch.setattr(admin_ops_module, "_BACKUP_FILES_DIR", str(tmp_path / "files"))

    # Create a fake backup dump file
    backup_file = tmp_path / "backup_20240101.dump"
    backup_file.write_bytes(b"fake pg_dump output")

    resp = await client.get(
        "/api/v1/admin/backups",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert any(entry["date"] == "20240101" for entry in data)


# ═══════════════════════════════════════════════════════════════════════════
# Reports API — download-not-ready, batch filter, report failure
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_download_report_not_ready(
    client: AsyncClient, admin_user, db_session
):
    """Downloading a report task that hasn't completed yet returns 409."""
    from app.models.export_task import ExportTask, ExportStatus
    task = ExportTask(
        report_type="reconciliation",
        status=ExportStatus.PENDING,
        created_by=admin_user.id,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}/download",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_generate_report_failure_captured(
    client: AsyncClient, admin_user, tmp_path, monkeypatch
):
    """When report generator raises, the task is marked FAILED (not a 500)."""
    import app.api.v1.reports as reports_module
    import app.reports.generator as gen_module
    monkeypatch.setattr(gen_module, "_EXPORT_ROOT", str(tmp_path))

    async def _boom(*args, **kwargs):
        raise RuntimeError("Simulated generator failure")

    monkeypatch.setattr(reports_module, "_run_report", _boom)

    resp = await client.post(
        "/api/v1/reports/generate/reconciliation",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# Registrations — financial admin list, batch/status filters, forbidden roles
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_registrations_as_financial_admin(
    client: AsyncClient, finance_user, applicant_user, admin_user, db_session
):
    """Financial admin can list non-draft registrations."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.get(
        "/api/v1/registrations",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_registrations_with_batch_id_filter(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """batch_id query param filters registrations to that batch."""
    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.get(
        f"/api/v1/registrations?batch_id={batch.id}",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["batch_id"] == str(batch.id) for r in data["items"])


@pytest.mark.asyncio
async def test_list_registrations_with_status_filter(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """status query param filters registrations by status."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.get(
        "/api/v1/registrations?status=submitted",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["status"] == "submitted" for r in data["items"])


@pytest.mark.asyncio
async def test_create_registration_forbidden_for_reviewer(
    client: AsyncClient, reviewer_user, admin_user, db_session
):
    """Reviewers cannot create new registrations."""
    batch = await _make_batch(db_session, admin_user)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/registrations",
        json={
            "batch_id": str(batch.id),
            "title": "Reviewer Reg",
            "activity_type": "workshop",
            "description": "desc",
            "applicant_name": "Reviewer",
        },
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_already_submitted_registration(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Submitting a non-draft registration returns 409."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/submit",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_submit_registration_with_required_items_missing(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Submitting when required checklist items are missing returns 422."""
    batch = await _make_batch(db_session, admin_user)
    # Add a required checklist item
    item = ChecklistItem(batch_id=batch.id, label="Required Doc", is_required=True)
    db_session.add(item)

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Missing Items",
        activity_type="workshop",
        description="desc",
        applicant_name="Applicant",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/submit",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Metrics — forbidden for applicant, with alert threshold
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_metrics_forbidden_for_applicant(
    client: AsyncClient, applicant_user
):
    """Applicant role cannot access metrics endpoint."""
    resp = await client.get(
        "/api/v1/metrics",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_metrics_with_alert_threshold(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """Metrics with AlertThreshold in DB exercises the threshold check branch."""
    from app.models.notification import AlertThreshold, ComparisonOp

    threshold = AlertThreshold(
        metric_name="approval_rate",
        threshold_value=Decimal("50"),
        comparison=ComparisonOp.LT,
        updated_by=admin_user.id,
    )
    db_session.add(threshold)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/metrics",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "approval_rate" in data


@pytest.mark.asyncio
async def test_metrics_threshold_breached(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """When threshold is breached, metrics still returns 200 with breached field."""
    from app.models.notification import AlertThreshold, ComparisonOp

    # Create submitted reg so approval_rate > 0
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # Set threshold that WOULD be breached: approval_rate > 0 (no approved yet, so 0 > 0 is False)
    # Let's use LT: approval_rate < 100 will breach since 0% approved < 100%
    threshold = AlertThreshold(
        metric_name="approval_rate",
        threshold_value=Decimal("100"),
        comparison=ComparisonOp.LT,
        updated_by=admin_user.id,
    )
    db_session.add(threshold)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/metrics",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Materials — supplementary submit
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_supplementary_submit_success(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    """Supplementary submission succeeds when in the supplementary window."""
    import app.api.v1.materials as mat_module
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    # Create batch with past submission_deadline but future supplementary_deadline.
    # supplementary_deadline is a PG GENERATED column (submission_deadline + 72h),
    # so to land inside the supplementary window we put submission_deadline an
    # hour in the past → supplementary_deadline is ~71h in the future.
    batch = CollectionBatch(
        name="Supplementary Batch",
        submission_deadline=datetime.now(timezone.utc) - timedelta(hours=1),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    item = ChecklistItem(batch_id=batch.id, label="Supp Item", is_required=False)
    db_session.add(item)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Supplementary Reg",
        activity_type="workshop",
        description="Test",
        applicant_name="Supp Tester",
    )
    db_session.add(reg)
    await db_session.flush()

    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    await db_session.refresh(reg)
    await db_session.refresh(batch)

    pdf_content = b"%PDF-1.4 supplementary document content"
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit",
        files=[
            ("correction_reason", (None, "Additional documents found after deadline", "text/plain")),
            ("material_ids", (None, str(mat.id), "text/plain")),
            ("files", ("supp.pdf", io.BytesIO(pdf_content), "application/pdf")),
        ],
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 201
    versions = resp.json()
    assert len(versions) == 1
    assert versions[0]["version_number"] == 1


@pytest.mark.asyncio
async def test_supplementary_submit_missing_reason(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    """Supplementary submit without correction_reason returns 422."""
    import app.api.v1.materials as mat_module
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    # submission_deadline an hour past → computed supplementary_deadline ~71h future.
    batch = CollectionBatch(
        name="Supp Batch 2",
        submission_deadline=datetime.now(timezone.utc) - timedelta(hours=1),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    item = ChecklistItem(batch_id=batch.id, label="Item2", is_required=False)
    db_session.add(item)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="No Reason Reg",
        activity_type="workshop",
        description="Test",
        applicant_name="Tester",
    )
    db_session.add(reg)
    await db_session.flush()

    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit",
        files=[
            ("correction_reason", (None, "", "text/plain")),
            ("material_ids", (None, str(mat.id), "text/plain")),
            ("files", ("doc.pdf", io.BytesIO(b"%PDF test"), "application/pdf")),
        ],
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_supplementary_submit_mismatched_counts(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    """Supplementary submit with mismatched file/material_id counts returns 400."""
    import app.api.v1.materials as mat_module
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    # submission_deadline an hour past → computed supplementary_deadline ~71h future.
    batch = CollectionBatch(
        name="Mismatch Batch",
        submission_deadline=datetime.now(timezone.utc) - timedelta(hours=1),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    item = ChecklistItem(batch_id=batch.id, label="Item3", is_required=False)
    db_session.add(item)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Mismatch Reg",
        activity_type="workshop",
        description="Test",
        applicant_name="Tester",
    )
    db_session.add(reg)
    await db_session.flush()

    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)

    # Send 2 files but only 1 material_id
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit",
        files=[
            ("correction_reason", (None, "Some reason", "text/plain")),
            ("material_ids", (None, str(mat.id), "text/plain")),
            ("files", ("doc1.pdf", io.BytesIO(b"%PDF-1.4 one"), "application/pdf")),
            ("files", ("doc2.pdf", io.BytesIO(b"%PDF-1.4 two"), "application/pdf")),
        ],
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# Materials — reviewer blocked from draft, upload-info
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reviewer_cannot_list_draft_materials(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    """Reviewer cannot list materials for a draft registration."""
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Draft Reg",
        activity_type="seminar",
        description="desc",
        applicant_name="Applicant",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)

    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/materials",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_info_for_submitted_registration(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Upload info endpoint returns size info for a submitted registration."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/materials/upload-info",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "used_bytes" in data
    assert "limit_bytes" in data
    assert data["supplementary_eligible"] is False
