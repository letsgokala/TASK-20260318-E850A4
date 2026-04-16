"""Tests for financial management and over-budget confirmation."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection_batch import CollectionBatch
from app.models.financial import FundingAccount
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


async def _setup_account(db_session, admin_user, finance_user, budget="10000.00"):
    batch = CollectionBatch(
        name="Fin Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=admin_user.id,
        status=RegistrationStatus.SUBMITTED,
        title="FinTest",
        activity_type="test",
        description="test",
        applicant_name="Test",
    )
    db_session.add(reg)
    await db_session.flush()

    acct = FundingAccount(
        registration_id=reg.id,
        name="Test Fund",
        allocated_budget=Decimal(budget),
        created_by=finance_user.id,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)
    return acct


@pytest.mark.asyncio
async def test_create_transaction_under_budget(client, finance_user, admin_user, db_session):
    acct = await _setup_account(db_session, admin_user, finance_user)
    headers = make_token(finance_user)

    resp = await client.post(f"/api/v1/finance/accounts/{acct.id}/transactions", json={
        "type": "expense",
        "amount": "5000.00",
        "category": "travel",
    }, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_over_budget_requires_confirmation(client, finance_user, admin_user, db_session):
    acct = await _setup_account(db_session, admin_user, finance_user, budget="1000.00")
    headers = make_token(finance_user)

    # This exceeds 110% of budget (1100) — should get 409
    resp = await client.post(f"/api/v1/finance/accounts/{acct.id}/transactions", json={
        "type": "expense",
        "amount": "1200.00",
        "category": "equipment",
    }, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["detail"] == "over_budget"

    # Re-submit with confirmation
    resp = await client.post(f"/api/v1/finance/accounts/{acct.id}/transactions", json={
        "type": "expense",
        "amount": "1200.00",
        "category": "equipment",
        "over_budget_confirmed": True,
    }, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_applicant_cannot_access_finance(client, applicant_user, admin_user, finance_user, db_session):
    acct = await _setup_account(db_session, admin_user, finance_user)
    headers = make_token(applicant_user)

    resp = await client.get(f"/api/v1/finance/accounts/{acct.id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_account_summary(client, finance_user, admin_user, db_session):
    acct = await _setup_account(db_session, admin_user, finance_user, budget="5000.00")
    headers = make_token(finance_user)

    # Add income
    await client.post(f"/api/v1/finance/accounts/{acct.id}/transactions", json={
        "type": "income", "amount": "2000.00", "category": "grants",
    }, headers=headers)

    # Add expense
    await client.post(f"/api/v1/finance/accounts/{acct.id}/transactions", json={
        "type": "expense", "amount": "1000.00", "category": "travel",
    }, headers=headers)

    resp = await client.get(f"/api/v1/finance/accounts/{acct.id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["total_income"]) == 2000.0
    assert float(data["total_expenses"]) == 1000.0
    assert data["overspending"] is False
