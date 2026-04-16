"""Root conftest — sets env vars before ANY app module is imported.

The main test lane now runs against a real PostgreSQL instance (previously
SQLite with JSONB/INET/computed-column rewrites). The rewrites hid production
behaviors, so the audit flagged the SQLite lane as not validating the
production contract. DATABASE_URL defaults to a PG URL, but callers can
override it from the environment (docker-compose sets it to point at the
``test-db`` service).
"""
import os

os.environ["TESTING"] = "1"
# Use a fixed test Fernet key (valid base64-encoded 32-byte key)
os.environ["SENSITIVE_FIELD_KEY"] = "BR0H2dkd0K1VwmPdrGzwl3slTGLCx4R99DK6l_jU3T8="
# Default to PostgreSQL for the main lane. SQLite is no longer a supported
# backend for tests because it cannot exercise JSONB, INET, computed columns,
# native enums, or timestamptz round-trips — all production contracts the
# app depends on. Use ``setdefault`` so docker-compose (or a developer running
# locally) can point tests at their own Postgres by setting DATABASE_URL.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point_test",
)
# ``test_postgres_integration.py`` historically skipped itself unless
# ``TEST_DATABASE_URL_PG`` was set explicitly. Now that the main lane is PG,
# mirror DATABASE_URL so the integration tests run by default alongside the
# rest of the suite.
os.environ.setdefault("TEST_DATABASE_URL_PG", os.environ["DATABASE_URL"])
os.environ["SECRET_KEY"] = "test-secret-key-for-jwt-signing-1234567890abcdef"
os.environ["ENABLE_DUPLICATE_CHECK_API"] = "true"
