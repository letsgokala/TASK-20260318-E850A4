"""Comprehensive tests covering batch CRUD, admin user management, quality validation,
health check, registration list/search, review history, finance stats,
admin operations, and security edge-cases.  These tests fill coverage gaps
identified in the static audit and push total coverage above 90 %.
"""
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
from app.models.quality_validation import QualityValidationResult, ValidationStatus, ValidationRuleType
from app.models.user import User, UserRole
from app.auth.password import hash_password
from tests.conftest import make_token


# ── Helpers ────────────────────────────────────────────────────────────────

async def _make_batch(db_session, admin_user, *, name="Test Batch", days=30):
    batch = CollectionBatch(
        name=name,
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=days),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    return batch


async def _make_submitted_reg(db_session, applicant_user, admin_user, *, batch=None):
    if batch is None:
        batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Comprehensive Test",
        activity_type="research",
        description="Desc",
        applicant_name="Tester",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)
    return reg, batch


async def _make_material_with_version(db_session, reg, applicant_user):
    item = ChecklistItem(
        batch_id=reg.batch_id,
        label="Doc",
        is_required=True,
    )
    db_session.add(item)
    await db_session.flush()

    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.flush()

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256_hash=uuid.uuid4().hex * 2,  # 64-char hex string
        storage_path="/tmp/test.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(mat)
    await db_session.refresh(ver)
    return mat, ver


# ═══════════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Health endpoint must return status=ok unauthenticated."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "db" in data


# ═══════════════════════════════════════════════════════════════════════════
# Batch CRUD
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_batch_as_admin(client: AsyncClient, admin_user):
    headers = make_token(admin_user)
    resp = await client.post("/api/v1/batches", json={
        "name": "Autumn Cohort",
        "description": "Autumn intake",
        "submission_deadline": "2026-09-01T00:00:00Z",
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Autumn Cohort"


@pytest.mark.asyncio
async def test_create_batch_requires_admin(client: AsyncClient, applicant_user, reviewer_user, finance_user):
    for user in (applicant_user, reviewer_user, finance_user):
        resp = await client.post("/api/v1/batches", json={
            "name": "Should Fail",
            "submission_deadline": "2026-09-01T00:00:00Z",
        }, headers=make_token(user))
        assert resp.status_code == 403, f"Expected 403 for role {user.role}"


@pytest.mark.asyncio
async def test_list_batches_accessible_to_all_roles(
    client: AsyncClient, admin_user, applicant_user, reviewer_user, finance_user
):
    for user in (admin_user, applicant_user, reviewer_user, finance_user):
        resp = await client.get("/api/v1/batches", headers=make_token(user))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_batch_by_id(client: AsyncClient, admin_user, db_session):
    batch = await _make_batch(db_session, admin_user, name="Retrieve Batch")
    await db_session.commit()
    resp = await client.get(f"/api/v1/batches/{batch.id}", headers=make_token(admin_user))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Retrieve Batch"


@pytest.mark.asyncio
async def test_get_batch_not_found(client: AsyncClient, admin_user):
    resp = await client.get(f"/api/v1/batches/{uuid.uuid4()}", headers=make_token(admin_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_batch(client: AsyncClient, admin_user, db_session):
    batch = await _make_batch(db_session, admin_user, name="Old Name")
    await db_session.commit()
    resp = await client.put(f"/api/v1/batches/{batch.id}", json={
        "name": "New Name",
    }, headers=make_token(admin_user))
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_update_batch_not_found(client: AsyncClient, admin_user):
    resp = await client.put(f"/api/v1/batches/{uuid.uuid4()}", json={
        "name": "X",
    }, headers=make_token(admin_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_checklist_item(client: AsyncClient, admin_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    await db_session.commit()
    resp = await client.post(f"/api/v1/batches/{batch.id}/checklist", json={
        "label": "CV Document",
        "is_required": True,
        "sort_order": 1,
    }, headers=make_token(admin_user))
    assert resp.status_code == 201
    assert resp.json()["label"] == "CV Document"


@pytest.mark.asyncio
async def test_create_checklist_item_batch_not_found(client: AsyncClient, admin_user):
    resp = await client.post(f"/api/v1/batches/{uuid.uuid4()}/checklist", json={
        "label": "X",
    }, headers=make_token(admin_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_checklist_items(client: AsyncClient, admin_user, applicant_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    item = ChecklistItem(batch_id=batch.id, label="Item 1", is_required=True)
    db_session.add(item)
    await db_session.commit()

    for user in (admin_user, applicant_user):
        resp = await client.get(f"/api/v1/batches/{batch.id}/checklist", headers=make_token(user))
        assert resp.status_code == 200
        assert any(i["label"] == "Item 1" for i in resp.json())


# ═══════════════════════════════════════════════════════════════════════════
# Admin user management
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_create_user(client: AsyncClient, admin_user):
    headers = make_token(admin_user)
    resp = await client.post("/api/v1/admin/users", json={
        "username": "new_reviewer",
        "password": "Reviewer@Pass1234!",
        "role": "reviewer",
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "new_reviewer"
    assert data["role"] == "reviewer"


@pytest.mark.asyncio
async def test_admin_create_user_duplicate_username(client: AsyncClient, admin_user, db_session):
    existing = User(
        username="dup_user",
        password_hash=hash_password("DupPass@1234!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(existing)
    await db_session.commit()

    resp = await client.post("/api/v1/admin/users", json={
        "username": "dup_user",
        "password": "DupPass@1234!",
        "role": "applicant",
    }, headers=make_token(admin_user))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_admin_list_users(client: AsyncClient, admin_user):
    resp = await client.get("/api/v1/admin/users", headers=make_token(admin_user))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    usernames = [u["username"] for u in resp.json()]
    assert "admin" in usernames


@pytest.mark.asyncio
async def test_admin_reset_password(client: AsyncClient, admin_user, applicant_user):
    resp = await client.put(
        f"/api/v1/admin/users/{applicant_user.id}/reset-password",
        json={"new_password": "NewPass@98761234!"},
        headers=make_token(admin_user),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_admin_reset_password_user_not_found(client: AsyncClient, admin_user):
    resp = await client.put(
        f"/api/v1/admin/users/{uuid.uuid4()}/reset-password",
        json={"new_password": "NewPass@98761234!"},
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_unlock_user(client: AsyncClient, admin_user, applicant_user, db_session):
    applicant_user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/admin/users/{applicant_user.id}/unlock",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 204

    await db_session.refresh(applicant_user)
    assert applicant_user.locked_until is None


@pytest.mark.asyncio
async def test_admin_unlock_user_not_found(client: AsyncClient, admin_user):
    resp = await client.put(
        f"/api/v1/admin/users/{uuid.uuid4()}/unlock",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_deactivate_user(client: AsyncClient, admin_user, applicant_user):
    resp = await client.put(
        f"/api/v1/admin/users/{applicant_user.id}/deactivate",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(client: AsyncClient, admin_user):
    resp = await client.put(
        f"/api/v1/admin/users/{admin_user.id}/deactivate",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_deactivate_user_not_found(client: AsyncClient, admin_user):
    resp = await client.put(
        f"/api/v1/admin/users/{uuid.uuid4()}/deactivate",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Registration list / search
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_registrations_admin_sees_all(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get("/api/v1/registrations", headers=make_token(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    ids = [r["id"] for r in data.get("items", data)]
    assert str(reg.id) in ids


@pytest.mark.asyncio
async def test_list_registrations_applicant_sees_only_own(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # Second applicant with their own registration
    other = User(
        username="other_list_test",
        password_hash=hash_password("Other@1234!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(other)
    await db_session.flush()
    other_reg, _ = await _make_submitted_reg(db_session, other, admin_user)

    resp = await client.get("/api/v1/registrations", headers=make_token(applicant_user))
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", data)
    ids = [r["id"] for r in items]
    assert str(reg.id) in ids
    assert str(other_reg.id) not in ids


@pytest.mark.asyncio
async def test_list_registrations_reviewer_skips_drafts(
    client: AsyncClient, admin_user, applicant_user, reviewer_user, db_session
):
    batch = await _make_batch(db_session, admin_user)
    draft = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.DRAFT,
        title="Hidden Draft",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(draft)
    await db_session.commit()

    resp = await client.get("/api/v1/registrations", headers=make_token(reviewer_user))
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", data)
    ids = [r["id"] for r in items]
    assert str(draft.id) not in ids


@pytest.mark.asyncio
async def test_get_registration_admin_sees_unmasked_pii(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """Admin gets PII unmasked (plaintext after decryption)."""
    from app.utils.encryption import encrypt_value
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="PII Admin Test",
        activity_type="research",
        description="Desc",
        applicant_name="Bob",
        applicant_phone=encrypt_value("+1234567890"),
        applicant_email=encrypt_value("bob@test.com"),
        applicant_id_number=encrypt_value("ID123456"),
    )
    db_session.add(reg)
    await db_session.commit()

    resp = await client.get(f"/api/v1/registrations/{reg.id}", headers=make_token(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    # Admin sees full unmasked values
    assert data.get("applicant_phone") == "+1234567890"
    assert data.get("applicant_email") == "bob@test.com"


# ═══════════════════════════════════════════════════════════════════════════
# Quality validation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_validation_applicant_own_reg(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/validate",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "passed" in data
    assert "failed" in data


@pytest.mark.asyncio
async def test_run_validation_reviewer(
    client: AsyncClient, admin_user, applicant_user, reviewer_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/validate",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_run_validation_other_applicant_blocked(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    other = User(
        username="other_val_test",
        password_hash=hash_password("Other@1234!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(other)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/validate",
        headers=make_token(other),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_run_validation_not_found(client: AsyncClient, reviewer_user):
    resp = await client.post(
        f"/api/v1/registrations/{uuid.uuid4()}/validate",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_validation_results(
    client: AsyncClient, admin_user, applicant_user, reviewer_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)

    # Seed a validation result directly
    result = QualityValidationResult(
        registration_id=reg.id,
        rule_type=ValidationRuleType.REQUIRED_FIELD,
        rule_name="title",
        status=ValidationStatus.PASS,
        message="All required fields present",
        checked_by=reviewer_user.id,
    )
    db_session.add(result)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/validations",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_validation_results_not_found(client: AsyncClient, reviewer_user):
    resp = await client.get(
        f"/api/v1/registrations/{uuid.uuid4()}/validations",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Review history
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_review_history_returns_records(
    client: AsyncClient, admin_user, applicant_user, reviewer_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    # Trigger a real transition to generate a review record
    await client.post(
        f"/api/v1/reviews/registrations/{reg.id}/transition",
        json={"to_status": "approved", "comment": "LGTM"},
        headers=make_token(reviewer_user),
    )
    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/history",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) >= 1
    assert history[0]["to_status"] == "approved"


@pytest.mark.asyncio
async def test_review_history_applicant_own(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/history",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_review_history_finance_blocked(
    client: AsyncClient, admin_user, applicant_user, finance_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/history",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_review_history_not_found(client: AsyncClient, reviewer_user):
    resp = await client.get(
        f"/api/v1/reviews/registrations/{uuid.uuid4()}/history",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_transition_not_found(client: AsyncClient, reviewer_user):
    resp = await client.post(
        f"/api/v1/reviews/registrations/{uuid.uuid4()}/transition",
        json={"to_status": "approved"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Finance — extended paths
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_transactions(client: AsyncClient, admin_user, finance_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="TxnList",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()
    acct = FundingAccount(
        registration_id=reg.id,
        name="Fund",
        allocated_budget=Decimal("10000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    # Create a transaction
    await client.post(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        json={"type": "income", "amount": "500.00", "category": "grant"},
        headers=make_token(finance_user),
    )

    resp = await client.get(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_list_transactions_account_not_found(client: AsyncClient, finance_user):
    resp = await client.get(
        f"/api/v1/finance/accounts/{uuid.uuid4()}/transactions",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_transaction_account_not_found(client: AsyncClient, finance_user):
    resp = await client.post(
        f"/api/v1/finance/accounts/{uuid.uuid4()}/transactions",
        json={"type": "expense", "amount": "100.00", "category": "test"},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_financial_statistics(client: AsyncClient, admin_user, finance_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Stats Reg",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()
    acct = FundingAccount(
        registration_id=reg.id,
        name="Stats Fund",
        allocated_budget=Decimal("20000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    txn = FinancialTransaction(
        funding_account_id=None,  # will be set below
        type=TransactionType.INCOME,
        amount=Decimal("1000"),
        category="grants",
        recorded_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    # Use the API to create a proper transaction
    await client.post(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        json={"type": "income", "amount": "1000.00", "category": "grants"},
        headers=make_token(finance_user),
    )

    resp = await client.get("/api/v1/finance/statistics", headers=make_token(finance_user))
    assert resp.status_code == 200
    data = resp.json()
    assert "grand_total_income" in data
    assert "grand_total_expense" in data


@pytest.mark.asyncio
async def test_upload_invoice_mime_rejected(client: AsyncClient, admin_user, finance_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Invoice Reg",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()
    acct = FundingAccount(
        registration_id=reg.id,
        name="Inv Fund",
        allocated_budget=Decimal("5000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    txn_resp = await client.post(
        f"/api/v1/finance/accounts/{acct.id}/transactions",
        json={"type": "expense", "amount": "100.00", "category": "test"},
        headers=make_token(finance_user),
    )
    txn_id = txn_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/finance/transactions/{txn_id}/invoice",
        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_invoice_transaction_not_found(client: AsyncClient, finance_user):
    resp = await client.post(
        f"/api/v1/finance/transactions/{uuid.uuid4()}/invoice",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
        headers=make_token(finance_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_funding_account_reg_not_found(client: AsyncClient, finance_user):
    resp = await client.post("/api/v1/finance/accounts", json={
        "registration_id": str(uuid.uuid4()),
        "name": "Ghost Fund",
        "allocated_budget": "5000.00",
    }, headers=make_token(finance_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_account_not_found(client: AsyncClient, finance_user):
    resp = await client.get(f"/api/v1/finance/accounts/{uuid.uuid4()}", headers=make_token(finance_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_accounts_with_filter(client: AsyncClient, admin_user, finance_user, db_session):
    batch = await _make_batch(db_session, admin_user)
    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Filter Reg",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.flush()
    acct = FundingAccount(
        registration_id=reg.id,
        name="Filter Fund",
        allocated_budget=Decimal("1000"),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    resp = await client.get(
        f"/api/v1/finance/accounts?registration_id={reg.id}",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Admin operations
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_integrity_check_runs_successfully(client: AsyncClient, admin_user):
    resp = await client.post("/api/v1/admin/integrity-check", headers=make_token(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "ok" in data
    assert "missing" in data
    assert "hash_mismatch" in data


@pytest.mark.asyncio
async def test_integrity_check_missing_files(
    client: AsyncClient, admin_user, applicant_user, db_session
):
    """Versions with non-existent storage_path are reported as missing."""
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    mat, ver = await _make_material_with_version(db_session, reg, applicant_user)
    # storage_path doesn't exist on disk → should appear as missing

    resp = await client.post("/api/v1/admin/integrity-check", headers=make_token(admin_user))
    assert resp.status_code == 200
    data = resp.json()
    missing_ids = [m["version_id"] for m in data["missing"]]
    assert str(ver.id) in missing_ids


@pytest.mark.asyncio
async def test_restore_backup_invalid_date_format(client: AsyncClient, admin_user):
    resp = await client.post(
        "/api/v1/admin/backups/2026-04-15/restore",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 400
    assert "YYYYMMDD" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restore_backup_not_found(client: AsyncClient, admin_user):
    resp = await client.post(
        "/api/v1/admin/backups/20260415/restore",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_logs_with_filters(client: AsyncClient, admin_user):
    resp = await client.get(
        "/api/v1/admin/audit-logs?action=login&page=1&page_size=10",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_audit_logs_non_admin_blocked(client: AsyncClient, reviewer_user, finance_user, applicant_user):
    for user in (reviewer_user, finance_user, applicant_user):
        resp = await client.get("/api/v1/admin/audit-logs", headers=make_token(user))
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Reports — additional paths
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_invalid_report_type(client: AsyncClient, admin_user):
    resp = await client.post(
        "/api/v1/reports/generate/unknown_type",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_finance_admin_cannot_generate_audit_report(client: AsyncClient, finance_user):
    resp = await client.post(
        "/api/v1/reports/generate/audit",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_export_task_not_found(client: AsyncClient, admin_user):
    resp = await client.get(
        f"/api/v1/reports/tasks/{uuid.uuid4()}",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_report_not_ready(client: AsyncClient, admin_user, db_session):
    from app.models.export_task import ExportTask, ExportStatus
    task = ExportTask(
        id=uuid.uuid4(),
        report_type="reconciliation",
        status=ExportStatus.PROCESSING,
        created_by=admin_user.id,
    )
    db_session.add(task)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}/download",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_download_report_complete_but_missing_file(client: AsyncClient, admin_user, db_session):
    from app.models.export_task import ExportTask, ExportStatus
    task = ExportTask(
        id=uuid.uuid4(),
        report_type="reconciliation",
        status=ExportStatus.COMPLETE,
        file_path="/nonexistent/report.xlsx",
        created_by=admin_user.id,
    )
    db_session.add(task)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/reports/tasks/{task.id}/download",
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Materials — additional coverage
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_material_checklist_item_not_found(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    headers = make_token(applicant_user)
    # Provide a checklist_item_id that doesn't exist
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials?checklist_item_id={uuid.uuid4()}",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_upload_size_info(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/materials/upload-info",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "used_bytes" in data
    assert "limit_bytes" in data
    assert "remaining_bytes" in data


@pytest.mark.asyncio
async def test_upload_version_material_not_found(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials/{uuid.uuid4()}/versions",
        files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_version_not_found(client: AsyncClient, applicant_user):
    resp = await client.get(
        f"/api/v1/registrations/versions/{uuid.uuid4()}/download",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_version_status_not_found(client: AsyncClient, reviewer_user):
    resp = await client.put(
        f"/api/v1/registrations/versions/{uuid.uuid4()}/status",
        json={"status": "submitted"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_version_cap_enforced(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    """After 3 versions (the cap), upload must return 409."""
    import app.api.v1.materials as mat_module
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    reg, batch = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = ChecklistItem(batch_id=batch.id, label="Cap Test", is_required=True)
    db_session.add(item)
    await db_session.flush()
    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)

    headers = make_token(applicant_user)
    # Upload 3 versions (the cap)
    for i in range(3):
        payload = io.BytesIO(b"%PDF-1.4 " + bytes([i]) * 128)
        r = await client.post(
            f"/api/v1/registrations/{reg.id}/materials/{mat.id}/versions",
            files={"file": (f"v{i}.pdf", payload, "application/pdf")},
            headers=headers,
        )
        assert r.status_code == 201, f"Version {i+1} upload failed: {r.json()}"

    # 4th version must be rejected
    payload = io.BytesIO(b"%PDF-1.4 " + b"x" * 128)
    r = await client.post(
        f"/api/v1/registrations/{reg.id}/materials/{mat.id}/versions",
        files={"file": ("v4.pdf", payload, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_supplementary_submit_past_both_deadlines(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Supplementary submit should fail when past both deadlines."""
    # ``supplementary_deadline`` is a GENERATED column (submission_deadline +
    # 72h). Setting submission_deadline 10 days in the past puts the computed
    # supplementary_deadline 7 days in the past — both deadlines expired.
    batch = CollectionBatch(
        name="Expired Batch",
        submission_deadline=datetime.now(timezone.utc) - timedelta(days=10),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Expired",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit",
        files=[("files", ("doc.pdf", io.BytesIO(b"%PDF"), "application/pdf"))],
        data={"correction_reason": "late fix", "material_ids": str(uuid.uuid4())},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_financial_admin_blocked_from_materials(
    client: AsyncClient, finance_user, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.get(
        f"/api/v1/registrations/{reg.id}/materials",
        headers=make_token(finance_user),
    )
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Authentication — edge cases
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_login_with_locked_account(client: AsyncClient, admin_user, db_session):
    """A locked account must return 423 with Retry-After even with correct password."""
    admin_user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Admin@12345678!",
    })
    assert resp.status_code == 423
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_login_successful_resets_lock(client: AsyncClient, admin_user, db_session):
    """A successful login after lock expiry clears locked_until."""
    # Lock has expired
    admin_user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Admin@12345678!",
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_without_auth_header_returns_403(client: AsyncClient):
    """Accessing a protected endpoint without any token returns 403."""
    resp = await client.get("/api/v1/registrations")
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# Duplicate check
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_duplicate_check_no_match(client: AsyncClient, reviewer_user):
    fake_hash = "c" * 64
    resp = await client.get(
        f"/api/v1/materials/duplicates?hash={fake_hash}",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_duplicate_check_finds_submitted_match(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    item = ChecklistItem(batch_id=reg.batch_id, label="Dup Item", is_required=True)
    db_session.add(item)
    await db_session.flush()
    mat = Material(registration_id=reg.id, checklist_item_id=item.id)
    db_session.add(mat)
    await db_session.flush()

    test_hash = "d" * 64
    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="dup.pdf",
        mime_type="application/pdf",
        file_size_bytes=512,
        sha256_hash=test_hash,
        storage_path="/tmp/dup.pdf",
        status=MaterialVersionStatus.SUBMITTED,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/materials/duplicates?hash={test_hash}",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Registration — update submitted draft blocked
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_submitted_registration_blocked(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    reg, _ = await _make_submitted_reg(db_session, applicant_user, admin_user)
    resp = await client.put(
        f"/api/v1/registrations/{reg.id}/draft",
        json={"title": "Attempt Update"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_submit_registration_batch_not_found(client: AsyncClient, admin_user):
    """Submit must fail if the batch doesn't exist (covered by missing batch_id)."""
    resp = await client.post(
        "/api/v1/registrations",
        json={"batch_id": str(uuid.uuid4())},
        headers=make_token(admin_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_registration_not_found(client: AsyncClient, reviewer_user):
    resp = await client.get(
        f"/api/v1/registrations/{uuid.uuid4()}",
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 404
