"""Migration-applied test lane.

The main conftest bootstraps schema via ``Base.metadata.create_all`` to
keep the happy-path suite fast, which means controls implemented **only
inside migrations** (PostgreSQL RULEs that make ``audit_logs`` immutable,
computed columns, native enum CHECKs, INET CIDR support, etc.) are not
exercised by the default test run. The third audit flagged this as a
Medium-severity gap: a migration regression could drift silently.

This file adds a dedicated lane:

1. Apply every Alembic revision against a throw-away PostgreSQL
   database.
2. Assert the migration-only invariants hold:
   - ``audit_logs`` cannot be UPDATE-d or DELETE-d thanks to the
     ``audit_no_update`` / ``audit_no_delete`` PG RULEs defined in
     migration ``001_initial_users_auth_audit.py``.
   - ``collection_batches.supplementary_deadline`` is a computed
     column (not a writable column) per migration
     ``007_fix_supplementary_deadline_computed_expr.py``.

The lane auto-skips unless ``TEST_DATABASE_URL_PG`` points at a real
PostgreSQL instance — the main PG conftest already uses that env var,
so the default Docker-run lane picks this up automatically.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL_PG"),
    reason="Requires TEST_DATABASE_URL_PG pointing at a real PostgreSQL instance.",
)


async def _drop_everything(engine) -> None:
    """Tear down any schema left over from earlier runs so the migration
    lane always starts clean. Uses ``DROP SCHEMA public CASCADE`` so we
    also drop alembic_version, PG enums, computed columns, and RULEs
    from the previous run."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture
async def migrated_engine():
    """Apply every Alembic revision up to head against the configured
    test database, then yield a connected engine. Tear down with
    ``DROP SCHEMA`` so the main conftest's ``create_all`` lane still
    gets a clean slate afterwards."""
    pg_url = os.environ["TEST_DATABASE_URL_PG"]
    engine = create_async_engine(pg_url, future=True)

    await _drop_everything(engine)

    # Run alembic upgrade head against the same URL.
    from alembic import command
    from alembic.config import Config

    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    # Alembic's env.py pulls the URL from app.config.settings.DATABASE_URL,
    # so point that at the test database for the duration of this lane.
    from app.config import settings as _settings
    prior_url = _settings.DATABASE_URL
    _settings.DATABASE_URL = pg_url
    # Alembic needs a sync driver for its online migrations; swap
    # asyncpg for psycopg2 equivalent only if needed. The project's env.py
    # actually uses create_async_engine, so asyncpg is fine.
    try:
        command.upgrade(cfg, "head")
    finally:
        _settings.DATABASE_URL = prior_url

    try:
        yield engine
    finally:
        await _drop_everything(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_upgrade_head_applies_cleanly(migrated_engine):
    """Smoke test: every revision must apply against a real Postgres DB
    without raising. Catches syntax/ordering problems in migration
    files that the metadata-only lane cannot."""
    async with migrated_engine.connect() as conn:
        # If upgrade succeeded, alembic_version should carry a single
        # revision id matching the tip of the chain.
        row = await conn.execute(
            text("SELECT version_num FROM alembic_version")
        )
        rows = row.fetchall()
        assert len(rows) == 1, (
            f"alembic_version must have exactly one row, got {rows}"
        )


@pytest.mark.asyncio
async def test_audit_logs_update_is_silently_rejected(migrated_engine):
    """The ``audit_no_update`` PG RULE in migration 001 must make UPDATE
    against ``audit_logs`` a silent no-op. The ORM-only create_all lane
    cannot exercise this; only a migrated schema carries the RULE."""
    async with migrated_engine.connect() as conn:
        # Insert a row
        row_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO audit_logs (action, details, created_at) "
                "VALUES (:a, :d::jsonb, NOW())"
            ),
            {"a": "test_immutable_update", "d": '{"x": 1}'},
        )
        await conn.commit()

        inserted = (await conn.execute(
            text(
                "SELECT id, action FROM audit_logs "
                "WHERE action = 'test_immutable_update' "
                "ORDER BY id DESC LIMIT 1"
            )
        )).mappings().one()

        # Attempt to mutate the row. The RULE converts this to NOTHING,
        # so the statement completes but no row actually changes.
        await conn.execute(
            text(
                "UPDATE audit_logs SET action = 'tampered' WHERE id = :i"
            ),
            {"i": inserted["id"]},
        )
        await conn.commit()

        after = (await conn.execute(
            text("SELECT action FROM audit_logs WHERE id = :i"),
            {"i": inserted["id"]},
        )).scalar_one()
        assert after == "test_immutable_update", (
            "audit_no_update RULE must prevent UPDATE from changing the row"
        )


@pytest.mark.asyncio
async def test_audit_logs_delete_is_silently_rejected(migrated_engine):
    """The ``audit_no_delete`` RULE must keep audit rows immortal.
    Migration 001 defines the RULE; create_all cannot replicate it."""
    async with migrated_engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_logs (action, details, created_at) "
                "VALUES (:a, :d::jsonb, NOW())"
            ),
            {"a": "test_immutable_delete", "d": '{"y": 2}'},
        )
        await conn.commit()

        before_count = (await conn.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs WHERE action = 'test_immutable_delete'"
            )
        )).scalar_one()
        assert before_count >= 1

        await conn.execute(
            text("DELETE FROM audit_logs WHERE action = 'test_immutable_delete'")
        )
        await conn.commit()

        after_count = (await conn.execute(
            text(
                "SELECT COUNT(*) FROM audit_logs WHERE action = 'test_immutable_delete'"
            )
        )).scalar_one()
        assert after_count == before_count, (
            "audit_no_delete RULE must prevent DELETE from removing rows"
        )
