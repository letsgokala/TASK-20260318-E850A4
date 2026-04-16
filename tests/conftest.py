"""Shared fixtures for tests — runs against real PostgreSQL.

The previous version of this file rewrote PG-specific column types
(``JSONB``→``JSON``, ``INET``→``Text``) and stripped ``col.computed`` so the
schema could be created on SQLite. That lane passed, but it could not catch
production-only failures in the database contract — JSONB operators,
computed-column enforcement, native enum CHECKs, ``timestamptz`` round-trips,
and FK/unique enforcement all behaved differently under SQLite. The audit
report flagged this as a hard gap.

This rewrite drops the rewrite block entirely and points the main engine at
a real Postgres instance (``DATABASE_URL``). Each test gets a fresh schema
via ``Base.metadata.drop_all`` / ``create_all`` — slower than SQLite in memory
but the only way to exercise production behavior.
"""
import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Env vars are set in the root conftest.py (loaded before any app import)
# Force-reload config to ensure it picks up test env vars
from app import config as _cfg
_cfg.settings = _cfg.Settings()

# Reset encryption module to use the reloaded config
from app.utils import encryption as _enc
_enc._fernet = None
_enc._initialized = False

from app.database import Base, get_db
from app.main import app
from app.auth.password import hash_password
from app.auth.jwt import create_access_token
from app.models.user import User, UserRole


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    """Per-test engine against the configured PostgreSQL database.

    Drops and recreates the full schema around each test so every test starts
    from a clean, production-shaped schema (real JSONB, INET, computed
    columns, native enums — none of the SQLite rewrites).
    """
    engine = create_async_engine(_cfg.settings.DATABASE_URL, future=True)
    async with engine.begin() as conn:
        # Belt-and-suspenders: if a previous test left a partial schema
        # behind (e.g. crashed mid-teardown), drop_all cleans it up before
        # create_all. ``checkfirst=True`` is the default.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session):
    user = User(
        id=uuid.uuid4(),
        username="admin",
        password_hash=hash_password("Admin@12345678!"),
        role=UserRole.SYSTEM_ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def applicant_user(db_session):
    user = User(
        id=uuid.uuid4(),
        username="applicant1",
        password_hash=hash_password("Applicant@1234!"),
        role=UserRole.APPLICANT,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def reviewer_user(db_session):
    user = User(
        id=uuid.uuid4(),
        username="reviewer1",
        password_hash=hash_password("Reviewer@1234!"),
        role=UserRole.REVIEWER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def finance_user(db_session):
    user = User(
        id=uuid.uuid4(),
        username="finance1",
        password_hash=hash_password("Finance@12345!"),
        role=UserRole.FINANCIAL_ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def make_token(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}
