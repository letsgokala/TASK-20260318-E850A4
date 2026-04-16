# System Design — Activity Registration & Funding Audit Platform

## Overview

A web application for managing activity registrations, peer review, and funding accountability. Applicants submit activity proposals with supporting documents; reviewers evaluate submissions; financial admins track budgets and transactions. The platform generates audit and compliance reports and enforces data integrity through file hashing and encrypted PII storage.

---

## Architecture

### Technology Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI (Python 3.11+) |
| ORM | SQLAlchemy 2.x (async) |
| Database | PostgreSQL 16 |
| Async driver | asyncpg |
| Migrations | Alembic |
| Auth | JWT (python-jose) + bcrypt |
| PII encryption | Fernet (cryptography) |
| Frontend | Vue.js 3 SPA |
| Build tool | Vite |
| Testing | pytest-asyncio, httpx (ASGITransport) |
| Container | Docker + docker-compose |

### Component Diagram

```
┌──────────────┐        ┌─────────────────────────────────────────┐
│  Vue.js SPA  │ HTTPS  │            FastAPI Backend              │
│  (Vite/Nginx)│──────► │  /api/v1/*                              │
└──────────────┘        │                                         │
                        │  Routers: auth, admin, batches,         │
                        │  registrations, materials, reviews,     │
                        │  finance, metrics, reports, admin_ops   │
                        │                   │                     │
                        │           SQLAlchemy ORM                │
                        └───────────────────┼─────────────────────┘
                                            │
                                   ┌────────▼────────┐
                                   │   PostgreSQL    │
                                   │   (persisted    │
                                   │    volumes)     │
                                   └─────────────────┘
```

---

## Data Model

### Core Entities

**User** — platform accounts with roles: `applicant`, `reviewer`, `financial_admin`, `system_admin`

**CollectionBatch** — a submission campaign with a `submission_deadline` and an auto-computed `supplementary_deadline` (deadline + 72 hours, PostgreSQL computed column)

**ChecklistItem** — required/optional document slots attached to a batch

**Registration** — an applicant's activity proposal linked to a batch; progresses through a status state machine; supports a 4-step wizard with draft auto-save

**Material** — a slot linking a registration to a checklist item; can have multiple `MaterialVersion` records (file uploads)

**MaterialVersion** — a specific uploaded file; stores `sha256_hash`, `storage_path`, `file_size_bytes`, `original_filename`, and `status` (`pending_submission` → `submitted` | `needs_correction`)

**ReviewRecord** — an immutable log of each status transition (from/to status, reviewer, comment, timestamp)

**FundingAccount** — a budget account linked to a registration; tracks `allocated_budget`, income, expenses, and balance

**Transaction** — a financial record (income or expense) on a funding account, optionally with an attached invoice file

**ExportTask** — a background report generation job with `pending` → `running` → `completed` | `failed` states

**AuditLog** — append-only log of all significant user actions

---

## Security Design

### Authentication & Authorization

- JWT tokens are issued at `POST /api/v1/auth/login` and expire after a configurable duration (default 60 minutes)
- Passwords are hashed with bcrypt; plaintext is never stored
- Account lockout: configurable attempt limit within a rolling window (default 10 attempts / 5 minutes → 30-minute lockout)
- Role-based access control is enforced at the route handler level via FastAPI dependencies

### Transactional audit fail-closed

The audit log is written **in the same transaction** as the domain mutation. The middleware sets a per-request context on import-time; a SQLAlchemy `before_commit` event hook reads that context and stages an `AuditLog` row on the session before the commit flushes. If the audit-row insert fails, the whole unit of work rolls back and the client sees a 500 — a mutating request can never succeed without a matching audit row. Sensitive reads (material/report downloads) write their audit row and commit before streaming the file; a failure blocks the download under `AUDIT_FAIL_CLOSED=1`.

### PII Encryption

Sensitive applicant fields (`applicant_id_number`, `applicant_phone`, `applicant_email`) are encrypted at rest using symmetric Fernet encryption before being written to the database. `applicant_name` is PII-masked in responses based on role, but is stored in plaintext for sorting and reviewer search. The encryption key is provided via the `SENSITIVE_FIELD_KEY` environment variable.

### File Upload Security

- Allowed MIME types: `application/pdf`, `image/jpeg`, `image/png`
- Maximum file size: 20 MB per file
- Maximum total storage per registration: 200 MB
- File names are replaced with UUID-based names at storage time; the original filename is preserved in `original_filename` only for display
- Storage path is validated to remain under the configured storage root (path traversal prevention)
- File integrity is verified by storing and later re-checking the SHA-256 hash of every uploaded file

### Secret Validation

On startup the application validates that `SECRET_KEY` and `SENSITIVE_FIELD_KEY` are not set to any known placeholder value. This check is skipped when `TESTING=1`.

### Emergency Failure Log

Four code paths deliberately swallow their own exceptions so a downstream failure does not cascade into a user-facing 5xx:

| Path | Subsystem |
|---|---|
| `app/middleware/audit.py` | audit-log persistence |
| `app/api/v1/metrics.py` `check_and_notify_breaches` | alert emission |
| `app/api/v1/quality_validation.py` `auto_validate_on_submit` | validation persistence |
| `app/utils/encryption.py` `decrypt_value` | PII decryption |

Every suppressed failure writes an `ERROR` log line *and* appends a JSON record via `app/utils/emergency_log.py` to `/var/log/eagle_point/critical_failures.jsonl` (override with the `EMERGENCY_LOG_PATH` environment variable). Operators reviewing a host after an incident can recover the full failure history from that file even when centralized logging is unavailable.

#### Fail-closed modes (default)

Four env-var flags control whether the audit/decryption/validation/alert paths **fail-closed (the default)** — surfacing failures as 5xx — or fail-open, suppressing the failure so the caller's response still succeeds. Fail-closed is the default because the audit report flagged "silent success" on compliance-critical paths as a gap; operators who need availability over strict correctness can opt out per-path:

| Flag | Default | Effect when `"1"` (default) | Effect when `"0"` |
|---|---|---|---|
| `AUDIT_FAIL_CLOSED` | `"1"` | Audit row is written in the same transaction as the domain write; a failure rolls back the whole unit of work → 500 response, no committed mutation. | Transactional audit hook is skipped; the middleware writes a best-effort audit row post-response and surfaces any failure via `X-Audit-Log-Fallback: emergency-log`. |
| `DECRYPT_FAIL_CLOSED` | `"1"` | `decrypt_value()` raises on `InvalidToken` | Returns the raw ciphertext so reads don't break |
| `VALIDATION_FAIL_CLOSED` | `"1"` | `auto_validate_on_submit()` raises | Persists nothing but lets the submit succeed |
| `ALERT_FAIL_CLOSED` | `"1"` | `check_and_notify_breaches()` is invoked BEFORE the handler's commit and raises on emission failure; the whole unit of work (state change + notifications) rolls back atomically and the client sees a 500 — never a committed write with no matching alert | Alert-emission failure is swallowed; the write-side handler still succeeds |

Regardless of the flag setting, the emergency-log fallback is always written, so nothing disappears silently. Pinned by `tests/test_fail_closed_modes.py`.

---

## Registration State Machine

```
DRAFT ──submit──► SUBMITTED ──approve──► APPROVED
                      │ └────reject────► REJECTED
                      │ └────waitlist──► WAITLISTED
                      │                     └──promote──► PROMOTED_FROM_WAITLIST
                      └──supplementary-submit──► SUPPLEMENTED
                                                    └── (same transitions as SUBMITTED)
Any state ──cancel──► CANCELED
```

Supplementary submission is available for **one use only**, within 72 hours after the `submission_deadline` of the associated batch.

---

## Report Types

| Type | Description |
|---|---|
| `audit` | Full audit log export — one row per audit record with optional user/action/date filters (sheet: "Audit Log"). |
| `compliance` | Per-registration compliance workbook (sheet: "Compliance"). Columns: `Registration ID`, `Applicant Name`, `Status`, `Materials Complete`, `Review History Summary`, `Flagged Issues` (supplementary usage and duplicate-file counts). Optional `batch_id`, `from_date`, `to_date` filters. |
| `whitelist` | Two-sheet workbook — sheet 1 ("Approved Registrations") lists every registration in `approved` or `promoted_from_waitlist`; sheet 2 ("Approved Materials") lists the SHA-256, filename, and checklist label of every material version attached to those approved registrations. Optional `batch_id` filter. |
| `reconciliation` | Finance reconciliation — per-registration budget vs. income/expense/balance with overspending flag (sheet: "Reconciliation"). Optional `batch_id`, `from_date`, `to_date` filters. |

Reports are generated via `POST /api/v1/reports/generate/{report_type}`. Small result sets (≤ 5000 rows) are generated synchronously in-process and the response already carries `status=complete`; larger result sets dispatch to a FastAPI `BackgroundTask`. In either case the caller polls `GET /api/v1/reports/tasks/{task_id}` until `status` is `complete`, then downloads via `GET /api/v1/reports/tasks/{task_id}/download`. Report downloads are sensitive reads and are audited before the file is streamed (see "Transactional audit fail-closed").

---

## Configuration

All configuration is via environment variables (or a `.env` file). See `.env.example` for the full list.

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | (local dev URL) |
| `SECRET_KEY` | JWT signing key (min 64 chars) | **must be overridden** |
| `SENSITIVE_FIELD_KEY` | Fernet encryption key for PII | **must be overridden** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT lifetime | 60 |
| `LOCKOUT_ATTEMPT_LIMIT` | Failed login attempts before lockout | 10 |
| `LOCKOUT_WINDOW_MINUTES` | Window for counting attempts | 5 |
| `LOCKOUT_DURATION_MINUTES` | How long the lockout lasts | 30 |
| `ENABLE_DUPLICATE_CHECK_API` | Enable SHA-256 duplicate detection endpoint | false |
| `TESTING` | Skip secret validation (set to `"1"` in tests) | `"0"` |

---

## Development & Testing

### Running Tests

```bash
./run_tests.sh
```

Tests run inside Docker. The suite uses one canonical lane:

1. **Backend lane (PostgreSQL).** `pytest tests/` runs with coverage, executed inside the `tests` container against the `test-db` service (PostgreSQL 16). Every test gets a fresh schema via `Base.metadata.drop_all` / `create_all` — slower than the previous in-memory SQLite lane, but the only way to exercise production behavior: `JSONB` operators, `INET` CIDR queries, computed columns (e.g. `collection_batches.supplementary_deadline`), native enums, and `timestamptz` round-trips. The SQLite rewrites in the old conftest are gone. `tests/test_postgres_integration.py` runs as part of this lane.

2. **Frontend lane.** Vitest against `frontend/src/__tests__/` inside the same test image.

For manual runs outside Docker, point the backend at a reachable PostgreSQL 16 instance:

```bash
export TEST_DATABASE_URL_PG=postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point_test
pytest tests/test_postgres_integration.py -v
```

The PG-only integration file auto-skips when `TEST_DATABASE_URL_PG` is unset, so partial local runs continue to work.

Frontend tests run separately under Vitest:

```bash
cd frontend && npm install && npm test
```

### Local Development

```bash
docker compose up
```

The API and the bundled Vue SPA are both served at `http://localhost:8000`. FastAPI mounts the built SPA from `frontend/dist/` at startup; the Vite dev server is optional for local UI work and is not exposed by `docker-compose.yml`.

### Database Migrations

```bash
alembic upgrade head   # apply all pending migrations
alembic revision --autogenerate -m "description"  # create new migration
```
