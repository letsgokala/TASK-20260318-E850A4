"""Tests for registration CRUD, draft/submit, and PII masking."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection_batch import CollectionBatch
from tests.conftest import make_token


async def _create_batch(db_session: AsyncSession, admin_user) -> CollectionBatch:
    batch = CollectionBatch(
        name="Test Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.commit()
    await db_session.refresh(batch)
    return batch


@pytest.mark.asyncio
async def test_create_draft_registration(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)
    headers = make_token(applicant_user)

    resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
        "title": "My Activity",
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["title"] == "My Activity"


@pytest.mark.asyncio
async def test_update_draft(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)
    headers = make_token(applicant_user)

    create_resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
    }, headers=headers)
    reg_id = create_resp.json()["id"]

    resp = await client.put(f"/api/v1/registrations/{reg_id}/draft", json={
        "title": "Updated Title",
        "wizard_step": 2,
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Title"
    assert resp.json()["wizard_step"] == 2


@pytest.mark.asyncio
async def test_submit_fails_without_required_fields(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)
    headers = make_token(applicant_user)

    create_resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
    }, headers=headers)
    reg_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/registrations/{reg_id}/submit", headers=headers)
    assert resp.status_code == 422
    assert "validation_errors" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pii_masking_for_reviewer(client: AsyncClient, applicant_user, reviewer_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)
    app_headers = make_token(applicant_user)

    create_resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
        "title": "Test",
        "activity_type": "research",
        "description": "Desc",
        "applicant_name": "John Doe",
        "applicant_id_number": "123456789012",
        "applicant_phone": "+1234567890",
        "applicant_email": "john@example.com",
    }, headers=app_headers)
    reg_id = create_resp.json()["id"]

    # Submit it so reviewer can see it
    await client.put(f"/api/v1/registrations/{reg_id}/draft", json={
        "title": "Test", "activity_type": "research",
        "description": "Desc", "applicant_name": "John Doe",
    }, headers=app_headers)
    await client.post(f"/api/v1/registrations/{reg_id}/submit", headers=app_headers)

    # Reviewer reads it — PII should be masked
    rev_headers = make_token(reviewer_user)
    resp = await client.get(f"/api/v1/registrations/{reg_id}", headers=rev_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "****" in data["applicant_id_number"]
    assert "****" in data["applicant_phone"]
    assert "***@" in data["applicant_email"]


@pytest.mark.asyncio
async def test_applicant_cannot_see_other_registration(client: AsyncClient, applicant_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)

    # Create a second applicant
    from app.models.user import User, UserRole
    from app.auth.password import hash_password
    other = User(
        username="other_applicant",
        password_hash=hash_password("Other@12345678!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    other_headers = make_token(other)
    create_resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
    }, headers=other_headers)
    reg_id = create_resp.json()["id"]

    # Original applicant tries to access
    headers = make_token(applicant_user)
    resp = await client.get(f"/api/v1/registrations/{reg_id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_see_drafts(client: AsyncClient, applicant_user, reviewer_user, admin_user, db_session):
    batch = await _create_batch(db_session, admin_user)
    app_headers = make_token(applicant_user)

    create_resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
    }, headers=app_headers)
    reg_id = create_resp.json()["id"]

    rev_headers = make_token(reviewer_user)
    resp = await client.get(f"/api/v1/registrations/{reg_id}", headers=rev_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_pii_encrypted_at_rest_after_submit(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """PII fields must remain Fernet-encrypted in the DB after submit — not plaintext."""
    from sqlalchemy import select as sa_select, text
    from app.models.registration import Registration as RegModel

    batch = await _create_batch(db_session, admin_user)
    headers = make_token(applicant_user)

    # Create and fully populate a draft
    resp = await client.post("/api/v1/registrations", json={
        "batch_id": str(batch.id),
        "title": "PII Test",
        "activity_type": "research",
        "description": "Testing PII at rest",
        "applicant_name": "Alice Smith",
        "applicant_id_number": "ID-987654321",
        "applicant_phone": "+9991234567",
        "applicant_email": "alice@example.com",
    }, headers=headers)
    assert resp.status_code == 201
    reg_id = resp.json()["id"]

    # Submit it (fills in required fields via the draft)
    await client.put(f"/api/v1/registrations/{reg_id}/draft", json={
        "title": "PII Test",
        "activity_type": "research",
        "description": "Testing PII at rest",
        "applicant_name": "Alice Smith",
    }, headers=headers)
    submit_resp = await client.post(f"/api/v1/registrations/{reg_id}/submit", headers=headers)
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "submitted"

    # Read raw DB values — they must NOT equal the plaintext originals
    result = await db_session.execute(
        sa_select(RegModel).where(RegModel.id == uuid.UUID(reg_id))
    )
    db_reg = result.scalar_one()
    assert db_reg.applicant_id_number != "ID-987654321", "ID number stored as plaintext!"
    assert db_reg.applicant_phone != "+9991234567", "Phone stored as plaintext!"
    assert db_reg.applicant_email != "alice@example.com", "Email stored as plaintext!"
    # Fernet tokens start with 'gAAAAA'
    assert db_reg.applicant_id_number.startswith("gAAAAA"), "ID number not Fernet-encrypted!"
    assert db_reg.applicant_phone.startswith("gAAAAA"), "Phone not Fernet-encrypted!"
    assert db_reg.applicant_email.startswith("gAAAAA"), "Email not Fernet-encrypted!"
