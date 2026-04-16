"""Tests for authentication, lockout, and login success behavior."""
import pytest
from httpx import AsyncClient

from tests.conftest import make_token


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_user):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Admin@12345678!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, admin_user):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "WrongPassword@1!",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_username(client: AsyncClient):
    resp = await client.post("/api/v1/auth/login", json={
        "username": "nonexistent",
        "password": "Whatever@1234!",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user(client: AsyncClient, admin_user, db_session):
    admin_user.is_active = False
    await db_session.commit()

    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Admin@12345678!",
    })
    assert resp.status_code == 401
    assert "deactivated" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_lockout_after_10_failures(client: AsyncClient, admin_user):
    for i in range(10):
        resp = await client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "Wrong@Password1!",
        })
        assert resp.status_code == 401, f"Attempt {i+1} should fail with 401"

    # 11th attempt should be locked
    resp = await client.post("/api/v1/auth/login", json={
        "username": "admin",
        "password": "Wrong@Password1!",
    })
    assert resp.status_code == 423
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_protected_endpoint_requires_token(client: AsyncClient):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 403  # No Bearer token


@pytest.mark.asyncio
async def test_admin_endpoint_requires_admin_role(client: AsyncClient, applicant_user):
    headers = make_token(applicant_user)
    resp = await client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expired_token_is_rejected(client: AsyncClient, admin_user):
    """A JWT whose ``exp`` is in the past must be treated as unauthenticated.

    The audit flagged that we had no coverage for expired-token handling;
    without this test, a regression in JWT decoding (e.g. silently accepting
    stale tokens after a library upgrade) would slip through.
    """
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.config import settings

    expired_token = jwt.encode(
        {
            "sub": str(admin_user.id),
            "role": admin_user.role.value,
            # Expired 10 minutes ago.
            "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    resp = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    # An expired token must not grant access; the dependency raises 401/403
    # depending on whether the exception is mapped at the auth layer.
    assert resp.status_code in (401, 403), (
        f"Expired token should be rejected, got {resp.status_code}"
    )
