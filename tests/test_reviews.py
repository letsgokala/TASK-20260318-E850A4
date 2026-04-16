"""Tests for review workflow transitions and batch review."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection_batch import CollectionBatch
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


async def _submitted_registration(db_session, applicant_user, admin_user) -> Registration:
    batch = CollectionBatch(
        name="Review Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="Test",
        activity_type="research",
        description="Desc",
        applicant_name="Test User",
    )
    db_session.add(reg)
    await db_session.commit()
    await db_session.refresh(reg)
    return reg


@pytest.mark.asyncio
async def test_reviewer_approves_registration(client, reviewer_user, applicant_user, admin_user, db_session):
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    headers = make_token(reviewer_user)

    resp = await client.post(f"/api/v1/reviews/registrations/{reg.id}/transition", json={
        "to_status": "approved",
        "comment": "Looks good",
    }, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["to_status"] == "approved"


@pytest.mark.asyncio
async def test_invalid_transition_returns_409(client, reviewer_user, applicant_user, admin_user, db_session):
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    # Make it rejected first
    reg.status = RegistrationStatus.REJECTED
    await db_session.commit()

    headers = make_token(reviewer_user)
    resp = await client.post(f"/api/v1/reviews/registrations/{reg.id}/transition", json={
        "to_status": "approved",
    }, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_applicant_can_only_cancel(client, applicant_user, admin_user, db_session):
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    headers = make_token(applicant_user)

    # Cannot approve
    resp = await client.post(f"/api/v1/reviews/registrations/{reg.id}/transition", json={
        "to_status": "approved",
    }, headers=headers)
    assert resp.status_code == 403

    # Can cancel
    resp = await client.post(f"/api/v1/reviews/registrations/{reg.id}/transition", json={
        "to_status": "canceled",
    }, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_batch_review_partial_success(client, reviewer_user, applicant_user, admin_user, db_session):
    reg1 = await _submitted_registration(db_session, applicant_user, admin_user)

    # Create a second one that's already rejected (will fail)
    batch = CollectionBatch(
        name="Batch2",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg2 = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=RegistrationStatus.REJECTED,
        title="Already Rejected",
        activity_type="x",
        description="x",
        applicant_name="x",
    )
    db_session.add(reg2)
    await db_session.commit()
    await db_session.refresh(reg2)

    headers = make_token(reviewer_user)
    resp = await client.post("/api/v1/reviews/batch", json={
        "action": "approved",
        "comment": "Batch approve",
        "registration_ids": [str(reg1.id), str(reg2.id)],
    }, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 1


@pytest.mark.asyncio
async def test_allowed_transitions_endpoint(client, reviewer_user, applicant_user, admin_user, db_session):
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    headers = make_token(reviewer_user)

    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/allowed-transitions",
        headers=headers,
    )
    assert resp.status_code == 200
    allowed = resp.json()
    assert "approved" in allowed
    assert "rejected" in allowed
    assert "waitlisted" in allowed
    assert "canceled" in allowed


@pytest.mark.asyncio
async def test_finance_user_cannot_review(client, finance_user, applicant_user, admin_user, db_session):
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    headers = make_token(finance_user)

    resp = await client.post(f"/api/v1/reviews/registrations/{reg.id}/transition", json={
        "to_status": "approved",
    }, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_allowed_transitions_applicant_isolation(client, applicant_user, admin_user, db_session):
    """Applicant must not see allowed-transitions for another user's registration."""
    reg = await _submitted_registration(db_session, applicant_user, admin_user)

    # Create a second applicant
    from app.models.user import User, UserRole
    from app.auth.password import hash_password
    other = User(
        username="other_app2",
        password_hash=hash_password("Other@12345678!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    other_headers = make_token(other)
    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/allowed-transitions",
        headers=other_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_allowed_transitions_finance_user_forbidden(client, finance_user, applicant_user, admin_user, db_session):
    """Financial admin must not access the allowed-transitions endpoint."""
    reg = await _submitted_registration(db_session, applicant_user, admin_user)
    headers = make_token(finance_user)

    resp = await client.get(
        f"/api/v1/reviews/registrations/{reg.id}/allowed-transitions",
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_allowed_transitions_draft_blocked_for_reviewer(client, reviewer_user, applicant_user, admin_user, db_session):
    """Reviewer must not query allowed-transitions for a draft registration."""
    batch = CollectionBatch(
        name="Draft Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
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
    await db_session.commit()
    await db_session.refresh(draft_reg)

    headers = make_token(reviewer_user)
    resp = await client.get(
        f"/api/v1/reviews/registrations/{draft_reg.id}/allowed-transitions",
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_batch_review_limit_50(client, reviewer_user, applicant_user, admin_user, db_session):
    """Batch review of exactly 50 registrations must succeed without error."""
    batch = CollectionBatch(
        name="Big Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg_ids = []
    for i in range(50):
        r = Registration(
            batch_id=batch.id,
            applicant_id=applicant_user.id,
            status=RegistrationStatus.SUBMITTED,
            title=f"Reg {i}",
            activity_type="x",
            description="x",
            applicant_name="x",
        )
        db_session.add(r)
        await db_session.flush()
        reg_ids.append(str(r.id))
    await db_session.commit()

    headers = make_token(reviewer_user)
    resp = await client.post("/api/v1/reviews/batch", json={
        "action": "approved",
        "comment": "Bulk approve",
        "registration_ids": reg_ids,
    }, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["succeeded"] == 50
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_batch_review_over_limit_rejected(client, reviewer_user):
    """Batch review of 51+ registration IDs must be rejected at schema layer.

    The audit flagged that we only tested the "exactly 50" success path, not
    the 51-item rejection edge. This test pins the max_length=50 contract so
    a regression that bumps the cap without updating the pydantic schema is
    caught.
    """
    import uuid as _uuid

    fake_ids = [str(_uuid.uuid4()) for _ in range(51)]
    headers = make_token(reviewer_user)
    resp = await client.post(
        "/api/v1/reviews/batch",
        json={
            "action": "approved",
            "comment": "Too many",
            "registration_ids": fake_ids,
        },
        headers=headers,
    )
    # Pydantic's max_length validation surfaces as a 422, never a 200.
    assert resp.status_code == 422, (
        f"51-item batch should be rejected by schema validation, got {resp.status_code}"
    )
