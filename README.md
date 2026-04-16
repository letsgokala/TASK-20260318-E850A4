# Activity Registration & Funding Audit Platform

An integrated closed-loop management platform for applicants, reviewers, financial administrators, and system administrators. Built with FastAPI, PostgreSQL, and Vue.js. Designed for fully offline/local deployment.

## Architecture

```
repo/
├── app/                    # FastAPI backend
│   ├── api/v1/             # REST API endpoints
│   ├── auth/               # JWT, password hashing, role dependencies
│   ├── middleware/          # Audit logging, maintenance mode
│   ├── models/             # SQLAlchemy ORM models (15 tables)
│   ├── schemas/            # Pydantic request/response schemas
│   ├── reports/            # Excel report generation (openpyxl)
│   ├── utils/              # Encryption, PII masking
│   └── workflows/          # Review state machine
├── frontend/               # Vue 3 + Vite SPA
├── alembic/                # Database migrations (7 revisions)
├── scripts/                # Backup, env encryption helpers
├── docker-compose.yml      # App + PostgreSQL 16
└── Dockerfile
```

## Prerequisites

- Python 3.11+
- PostgreSQL 16
- Node.js 18+ (for frontend build)
- Docker & Docker Compose (recommended for deployment)

## Quick Start (Docker)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — generate a Fernet key:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Start services
docker-compose up -d

# 3. Run migrations
docker-compose exec app alembic upgrade head

# 4. Seed the first admin user
docker-compose exec app python -m app.seed_admin --username admin --password 'YourP@ssw0rd!!'

# 5. Access the application
# API:      http://localhost:8000/api/v1/health
# Docs:     http://localhost:8000/docs
# Frontend: http://localhost:8000/
```

## Quick Start (Local Development)

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your PostgreSQL connection and Fernet key

# 3. Run migrations
alembic upgrade head

# 4. Seed admin
python -m app.seed_admin --username admin --password 'YourP@ssw0rd!!'

# 5. Build the frontend first (FastAPI mounts dist/ at startup)
cd frontend
npm install
npm run build
cd ..

# 6. Start the server (frontend/dist/ must exist before this step)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL async connection string | Yes |
| `SECRET_KEY` | JWT signing key (64+ random chars) | Yes |
| `SENSITIVE_FIELD_KEY` | Fernet key for PII encryption at rest | Yes |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT token lifetime (default: 60) | No |
| `ENABLE_DUPLICATE_CHECK_API` | Enable `/materials/duplicates` endpoint (default: false) | No |

## Running Tests

The canonical way to run tests is inside Docker (required by the submission harness):

```bash
./run_tests.sh                  # full suite
./run_tests.sh -k test_auth     # filter by test name
```

For local development only (requires a local PostgreSQL reachable at the URL
in `DATABASE_URL`):

```bash
pip install pytest pytest-asyncio httpx asyncpg pytest-cov
DATABASE_URL=postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point_test \
    pytest tests/ -v --cov=app --cov-report=term-missing
```

### Test lanes

`./run_tests.sh` runs two lanes:

1. **Backend lane (PostgreSQL)** — `pytest tests/` with coverage, executed
   inside the `tests` container against the `test-db` service (Postgres 16).
   This lane exercises production behaviors that the old SQLite lane could
   not: `JSONB` operators, `INET` CIDR queries, computed columns, native
   enums, and `timestamptz` round-trips. `tests/test_postgres_integration.py`
   runs as part of this lane.
2. **Frontend lane** — Vitest against `frontend/src/__tests__/` inside the
   same test image.

Opt-outs when needed:

```bash
FRONTEND_TESTS=1 ./run_tests.sh -k x  # force frontend lane with filtered pytest
```

Or outside Docker with a local PG:

```bash
export TEST_DATABASE_URL_PG=postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point_test
pytest tests/test_postgres_integration.py -v
```

The file-level tests auto-skip when `TEST_DATABASE_URL_PG` is unset so local runs without a PG continue to pass.

### Frontend tests

```bash
cd frontend
npm install
npm test
```

Frontend tests use Vitest under jsdom and cover the batch-review comment plumbing, finance statistics filters, transaction category validation, the POST verb for report generation, and the checklist-label display.

## API Overview

| Area | Prefix | Key Endpoints |
|------|--------|---------------|
| Auth | `/api/v1/auth` | Login |
| Admin | `/api/v1/admin` | User CRUD, backups, restore, integrity check, audit logs |
| Batches | `/api/v1/batches` | Collection batch + checklist management |
| Registrations | `/api/v1/registrations` | Draft/submit wizard, PII-masked detail, paginated list |
| Materials | `/api/v1/registrations/...` | File upload (PDF/JPG/PNG), versioning, correction workflow, download |
| Reviews | `/api/v1/reviews` | State transitions, batch review (up to 50), history |
| Finance | `/api/v1/finance` | Funding accounts, transactions with over-budget confirmation, statistics |
| Metrics | `/api/v1/metrics` | Approval/correction/overspending rates, alert thresholds |
| Notifications | `/api/v1/notifications` | In-app alerts with read/unread |
| Reports | `/api/v1/reports` | Excel export (reconciliation, audit, compliance, whitelist) |

Full interactive API documentation is available at `/docs` (Swagger UI).

## Roles

| Role | Capabilities |
|------|-------------|
| `applicant` | Create/edit registrations, upload materials, view own data |
| `reviewer` | Review registrations, batch approve/reject, set material correction status |
| `financial_admin` | Manage funding accounts, record transactions, generate reports |
| `system_admin` | Full access: user management, backups, restore, thresholds, audit logs |

## Security

- Passwords: bcrypt with work factor 12
- Lockout: 10 failed attempts in 5 minutes triggers 30-minute lock
- PII: Fernet-encrypted at rest, role-based masking in API responses
- Audit: Immutable append-only log of all mutating operations
- Config: supports **two deployment modes** — see "Encrypted sensitive configuration" below for the operator flow.
  - **Plaintext** (default): the process reads a plaintext `.env` (or real environment variables) at startup. Suitable for local dev and for deployments where a secret manager injects env vars directly.
  - **Encrypted**: the container ships with `age` installed and a runtime entrypoint (`scripts/entrypoint.sh`) that decrypts an `.env.age` to `/app/.env` at container start, then hands off to `uvicorn`. The plaintext file is written mode 0600 and removed on container exit. Either an age identity file (`ENV_AGE_KEY_FILE`) or a passphrase (`ENV_AGE_PASSPHRASE`) can be used as the decryption key.

### Emergency failure log

Four paths (audit-log persistence, alert emission, validation persistence, and PII decryption) mirror every failure as a JSON line to `/var/log/eagle_point/critical_failures.jsonl` (override with `EMERGENCY_LOG_PATH`). This gives operators a recoverable record even in deployments without a centralized log sink — and in fail-open mode (see below) it is the only record, since the user-facing response succeeds.

### Fail-closed modes (default)

The audit, PII-decryption, and validation-persistence paths are **fail-closed by default**: a downstream failure propagates to the client as a 500 instead of silently succeeding. This closes the "silent success" compliance gap flagged by the audit report. Operators who need the API to remain available under dependency outages (e.g. non-regulated tenants, incident mitigation) can set any of these to `"0"` to fall back to the prior fail-open behavior:

| Flag | Default | Behavior when `"1"` (default) | Behavior when `"0"` |
|---|---|---|---|
| `AUDIT_FAIL_CLOSED` | `"1"` | Mutating requests fail with 500 when the audit-log write fails | Response succeeds with `X-Audit-Log-Fallback: emergency-log` header |
| `DECRYPT_FAIL_CLOSED` | `"1"` | PII reads fail with 500 when decryption fails | Raw ciphertext is returned |
| `VALIDATION_FAIL_CLOSED` | `"1"` | Submit fails when validation can't be persisted | Submit succeeds without a validation record |
| `ALERT_FAIL_CLOSED` | `"1"` | Write-side handlers (reviews, finance transactions) fail with 500 when alert emission fails, so missed threshold breaches never translate into silently-successful writes | Handler succeeds without emitting the alert |

The emergency log fallback always runs regardless of mode.

## Encrypted sensitive configuration

When `.env` contains production secrets you do not want to leave on
disk in plaintext between container restarts, the image's runtime
entrypoint can decrypt an age-encrypted `.env.age` to `/app/.env` at
container start and remove it on exit.

Operator flow:

```bash
# 1. Encrypt the plaintext .env in place with a passphrase or identity.
./scripts/encrypt_env.sh              # writes .env.age, keeps .env for now
rm .env                               # plaintext no longer needed on disk

# 2. Choose a keying mode and provide it to the container.
#
# Passphrase mode — quick but weaker:
export ENV_AGE_PASSPHRASE='correct horse battery staple'
docker compose up
#
# Identity mode (recommended) — stronger, easier to rotate:
mkdir -p secrets
age-keygen -o secrets/env-age.key                 # writes an age identity
age --identity secrets/env-age.key --decrypt ... # test
# Mount the identity into the container and point the entrypoint at it:
export ENV_AGE_KEY_FILE=/run/secrets/env-age.key
# (docker-compose.yml has an inline comment block showing the volume/env
# stanza to uncomment when running in identity mode.)
```

At container start, `scripts/entrypoint.sh` detects the presence of
`/app/.env.age`, decrypts it to `/app/.env` with `age --decrypt`, sets
mode 0600, exports its entries, and exec's `uvicorn`. A `trap` deletes
the plaintext file when the container exits so no decrypted secret is
left on the container filesystem.

Decryption fails fast at startup with a clear message if `.env.age` is
present but neither `ENV_AGE_KEY_FILE` nor `ENV_AGE_PASSPHRASE` is set,
preventing the process from starting against stale plaintext.

## Backups

Daily backups run via cron at 02:00 (`scripts/backup.sh`):
- Database: `pg_dump` to `/backups/db/backup_YYYYMMDD.dump`
- Files: `rsync` of `/storage/materials/` to `/backups/files/YYYYMMDD/`
- 30-day rolling retention

One-click restore via admin API or UI:
```
POST /api/v1/admin/backups/{date}/restore
```

## Key Rotation

To rotate the PII encryption key:
```bash
python -m app.rotate_key --old-key <current-fernet-key> --new-key <new-fernet-key>
```
Then update `SENSITIVE_FIELD_KEY` in `.env` and restart.
