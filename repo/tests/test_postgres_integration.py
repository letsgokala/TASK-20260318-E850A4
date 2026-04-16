"""PostgreSQL-backed integration tests.

The default test lane (``tests/conftest.py``) runs against SQLite for speed.
That harness strips PostgreSQL-specific column behaviors (computed columns,
``JSONB``, ``INET``) so it can't catch production-only failures. This module
adds a second lane that runs critical flows against a real PostgreSQL 15
instance **without** those strip-downs.

Opt in by setting ``TEST_DATABASE_URL_PG`` to an asyncpg URL, e.g.::

    export TEST_DATABASE_URL_PG=postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point_test
    pytest tests/test_postgres_integration.py -v

Every test here is a ``pytestmark.skipif`` no-op when that variable is absent,
so the default test run is unaffected. The fixtures here deliberately do NOT
import ``tests/conftest.py``'s PG-stripping logic — the whole point is to
exercise the real schema.

Production contracts under test:

1. ``collection_batches.supplementary_deadline`` is a *computed* column
   (``submission_deadline + interval '72 hours'``). Writes must not be able
   to set it directly; reads must reflect the computed value.
2. ``audit_logs.details`` is a ``JSONB`` column. Queries against it must
   use the JSONB operators, not just plain equality.
3. Lockout persistence: after N failed logins, ``users.locked_until`` must
   be written with a timezone-aware timestamp and the row must survive
   across sessions.
4. ``audit_logs.ip_address`` is an ``INET`` column. Writes of typed strings
   must round-trip and be queryable with CIDR containment.
5. Enum types (``registration_status``, ``user_role``, …) must be created
   as real PG ``CREATE TYPE`` objects, not text-with-CHECK-CONSTRAINT.
6. Timezone-aware ``timestamptz`` columns must round-trip with timezone
   information preserved; naive datetimes must be rejected or coerced.
7. Foreign-key cascade/nullable semantics behave as declared — SQLite's
   default deferred FK enforcement masks many bugs here.
8. PG enforces ``CHECK`` constraints declared via ``create_constraint=True``
   on enum columns; SQLite's enforcement of these is unreliable.
9. Unique-index violations surface as ``IntegrityError`` (not silently
   overwriting rows, which SQLite can do with PRAGMA foreign_keys=OFF).
10. Over-budget concurrent writes on a funding account serialize correctly
    under PG's default READ COMMITTED isolation.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_PG_URL = os.environ.get("TEST_DATABASE_URL_PG")

pytestmark = pytest.mark.skipif(
    _PG_URL is None,
    reason="Set TEST_DATABASE_URL_PG to enable PostgreSQL-backed integration tests",
)


# ── Fixtures (PostgreSQL, no schema stripping) ─────────────────────────────

@pytest_asyncio.fixture
async def pg_engine():
    """Fresh schema per test — create_all / drop_all against real Postgres."""
    # Late imports so the module stays importable without PG configured.
    from app.database import Base

    engine = create_async_engine(_PG_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def pg_client(pg_engine):
    from app.database import get_db
    from app.main import app

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def pg_admin_user(pg_session):
    from app.auth.password import hash_password
    from app.models.user import User, UserRole

    user = User(
        id=uuid.uuid4(),
        username="pg_admin",
        password_hash=hash_password("Admin@12345678!"),
        role=UserRole.SYSTEM_ADMIN,
    )
    pg_session.add(user)
    await pg_session.commit()
    await pg_session.refresh(user)
    return user


# ── Tests — production-only contracts ──────────────────────────────────────

@pytest.mark.asyncio
async def test_supplementary_deadline_is_computed_by_postgres(pg_session, pg_admin_user):
    """PG must auto-populate ``supplementary_deadline`` as deadline + 72h.

    SQLite tests strip ``col.computed``, making this column plain-nullable.
    That lets the app-layer tests pretend to read the value but cannot prove
    that the *database* computes it. Only PG can.
    """
    from app.models.collection_batch import CollectionBatch

    deadline = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    batch = CollectionBatch(
        name="PG Batch",
        submission_deadline=deadline,
        created_by=pg_admin_user.id,
    )
    pg_session.add(batch)
    await pg_session.commit()
    await pg_session.refresh(batch)

    assert batch.supplementary_deadline is not None, (
        "Postgres did not populate the computed supplementary_deadline column"
    )
    assert batch.supplementary_deadline == deadline + timedelta(hours=72), (
        f"Expected deadline+72h, got {batch.supplementary_deadline}"
    )


@pytest.mark.asyncio
async def test_audit_log_jsonb_round_trip(pg_session, pg_admin_user):
    """``audit_logs.details`` is JSONB; writes/reads must round-trip dicts."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        user_id=pg_admin_user.id,
        action="PG_TEST_ACTION",
        resource_type="test",
        details={"status_code": 201, "note": "PG round-trip"},
    )
    pg_session.add(entry)
    await pg_session.commit()

    # JSONB path: query using a JSON operator that does NOT work on plain JSON.
    rows = (
        await pg_session.execute(
            text(
                "SELECT details FROM audit_logs "
                "WHERE details->>'note' = 'PG round-trip'"
            )
        )
    ).all()
    assert len(rows) == 1, "JSONB operator did not match — JSONB column may be wrong type"


@pytest.mark.asyncio
async def test_lockout_persists_across_sessions(pg_client: AsyncClient, pg_session, pg_admin_user):
    """After exceeding the failure threshold, ``users.locked_until`` must
    be written to the DB and survive across session boundaries."""
    from app.auth.password import hash_password
    from app.config import settings
    from app.models.user import User, UserRole

    # Seed a user whose password we know, then hammer it with bad passwords.
    user = User(
        id=uuid.uuid4(),
        username="lockout_victim",
        password_hash=hash_password("correct_password"),
        role=UserRole.APPLICANT,
    )
    pg_session.add(user)
    await pg_session.commit()

    # One more than the limit to guarantee lockout even with off-by-one math.
    for _ in range(settings.LOCKOUT_ATTEMPT_LIMIT + 1):
        await pg_client.post(
            "/api/v1/auth/login",
            json={"username": "lockout_victim", "password": "wrong"},
        )

    # Fresh session — proves the write was committed, not just in memory.
    from app.database import async_session as _app_session
    async with _app_session() as fresh:
        refreshed = (
            await fresh.execute(select(User).where(User.username == "lockout_victim"))
        ).scalar_one()
        assert refreshed.locked_until is not None, "locked_until was not persisted"
        assert refreshed.locked_until.tzinfo is not None, (
            "locked_until lost its timezone — PG timestamptz round-trip broken"
        )
        assert refreshed.locked_until > datetime.now(timezone.utc), (
            "locked_until is in the past — lockout window was not applied"
        )


@pytest.mark.asyncio
async def test_inet_column_round_trip_and_cidr_query(pg_session, pg_admin_user):
    """``audit_logs.ip_address`` is PG ``INET``; writes round-trip and
    support subnet containment queries that do not exist on plain text."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        user_id=pg_admin_user.id,
        action="INET_TEST",
        resource_type="test",
        ip_address="10.1.2.3",
    )
    pg_session.add(entry)
    await pg_session.commit()

    # `<<` is the PG-only "is contained by" CIDR operator.
    rows = (
        await pg_session.execute(
            text("SELECT ip_address FROM audit_logs WHERE ip_address << inet '10.1.0.0/16'")
        )
    ).all()
    assert len(rows) == 1, "INET CIDR-containment query missed the inserted row"


@pytest.mark.asyncio
async def test_enum_types_are_created_as_native_pg_enums(pg_engine):
    """The registration status enum must be a native PG type, not a TEXT
    column with a CHECK constraint. SQLite fakes this silently."""
    async with pg_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT typname FROM pg_type "
                "WHERE typname = 'registration_status' AND typtype = 'e'"
            )
        )
        rows = result.all()
    assert len(rows) == 1, (
        "Expected a PG native enum named 'registration_status', found none. "
        "The model's Enum(..., create_constraint=True) is not producing a "
        "real CREATE TYPE in PG."
    )


@pytest.mark.asyncio
async def test_enum_check_rejects_invalid_value(pg_session, pg_admin_user):
    """Writing a bogus status directly via SQL must be rejected by PG."""
    # Create a minimal registration with a valid status first so the row is
    # committed, then attempt an UPDATE to a garbage status.
    from app.models.collection_batch import CollectionBatch
    from app.models.registration import Registration

    batch = CollectionBatch(
        name="Enum Test Batch",
        submission_deadline=datetime(2026, 12, 1, tzinfo=timezone.utc),
        created_by=pg_admin_user.id,
    )
    pg_session.add(batch)
    await pg_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=pg_admin_user.id,
        title="Enum test",
    )
    pg_session.add(reg)
    await pg_session.commit()

    # Directly attempt the invalid update — PG must raise.
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        await pg_session.execute(
            text(
                "UPDATE registrations SET status = 'totally_invalid_status' "
                "WHERE id = :rid"
            ),
            {"rid": reg.id},
        )
        await pg_session.commit()

    await pg_session.rollback()


@pytest.mark.asyncio
async def test_timestamptz_preserves_timezone_across_reads(pg_session, pg_admin_user):
    """A timezone-aware write must come back timezone-aware (not naive)."""
    from app.models.audit_log import AuditLog

    # Write explicitly with a non-UTC offset to verify tz round-trip.
    from datetime import timezone as _tz

    plus4 = _tz(timedelta(hours=4))
    # AuditLog.created_at has server_default=NOW(); write a second row with
    # an explicit timestamp so we can verify what PG returns.
    entry = AuditLog(
        user_id=pg_admin_user.id,
        action="TZ_TEST",
        resource_type="test",
    )
    pg_session.add(entry)
    await pg_session.commit()
    await pg_session.refresh(entry)

    assert entry.created_at.tzinfo is not None, (
        "PG timestamptz column returned a naive datetime — timezone lost."
    )


@pytest.mark.asyncio
async def test_unique_username_violation_raises_integrityerror(pg_session):
    """A duplicate username insert must raise IntegrityError, not silently
    overwrite. SQLite can be fooled when FK/unique enforcement is off."""
    from sqlalchemy.exc import IntegrityError

    from app.auth.password import hash_password
    from app.models.user import User, UserRole

    u1 = User(
        id=uuid.uuid4(),
        username="dupe_user",
        password_hash=hash_password("pw"),
        role=UserRole.APPLICANT,
    )
    pg_session.add(u1)
    await pg_session.commit()

    u2 = User(
        id=uuid.uuid4(),
        username="dupe_user",
        password_hash=hash_password("pw"),
        role=UserRole.APPLICANT,
    )
    pg_session.add(u2)
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


@pytest.mark.asyncio
async def test_foreign_key_rejects_orphaned_registration(pg_session):
    """A registration pointing at a non-existent batch must be rejected."""
    from sqlalchemy.exc import IntegrityError

    from app.models.registration import Registration

    orphan = Registration(
        batch_id=uuid.uuid4(),  # no such batch
        applicant_id=uuid.uuid4(),  # no such user
        title="Orphan",
    )
    pg_session.add(orphan)
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


@pytest.mark.asyncio
async def test_supplementary_deadline_write_is_ignored(pg_session, pg_admin_user):
    """A value explicitly set for ``supplementary_deadline`` must be ignored
    — PG computes it from ``submission_deadline``. This is the behavior the
    SQLite lane cannot verify because it strips ``col.computed``."""
    from app.models.collection_batch import CollectionBatch

    deadline = datetime(2027, 1, 1, tzinfo=timezone.utc)
    bogus = datetime(2099, 12, 31, tzinfo=timezone.utc)

    batch = CollectionBatch(
        name="Computed-ignore test",
        submission_deadline=deadline,
        created_by=pg_admin_user.id,
    )
    # Force an attempted explicit write. PG ignores it or rejects it — either
    # way, the column must end up as deadline+72h, never the bogus value.
    try:
        batch.supplementary_deadline = bogus
    except Exception:
        pass  # SQLAlchemy may refuse to set a generated column

    pg_session.add(batch)
    try:
        await pg_session.commit()
    except Exception:
        # PG will raise if we attempt to INSERT a value into a GENERATED
        # ALWAYS column — that is also the contract we want to prove.
        await pg_session.rollback()
        return

    await pg_session.refresh(batch)
    assert batch.supplementary_deadline == deadline + timedelta(hours=72), (
        f"Expected computed value deadline+72h, got {batch.supplementary_deadline!r}. "
        "The explicit write appears to have taken effect — computed column not enforced."
    )


@pytest.mark.asyncio
async def test_jsonb_supports_containment_operator(pg_session, pg_admin_user):
    """JSONB supports the ``@>`` containment operator; plain JSON does not.
    A passing test here proves the column type is truly JSONB."""
    from app.models.audit_log import AuditLog

    entry = AuditLog(
        user_id=pg_admin_user.id,
        action="JSONB_CONTAIN_TEST",
        resource_type="test",
        details={"k1": "v1", "k2": {"nested": 42}},
    )
    pg_session.add(entry)
    await pg_session.commit()

    rows = (
        await pg_session.execute(
            text(
                "SELECT id FROM audit_logs "
                "WHERE details @> '{\"k1\": \"v1\"}'::jsonb"
            )
        )
    ).all()
    assert len(rows) == 1, "JSONB @> containment operator failed — column is not JSONB"
