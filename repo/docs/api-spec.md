# API Specification — Activity Registration & Funding Audit Platform

Base path: `/api/v1`

Content type for JSON endpoints: `application/json`. File uploads use `multipart/form-data`.
All endpoints require a Bearer JWT token in the `Authorization` header unless noted.

Status-code convention:
- `200 OK` — successful read / non-creating mutation
- `201 Created` — resource created
- `202 Accepted` — long-running work started
- `204 No Content` — successful mutation with no body
- `400 Bad Request` — malformed input or invalid business argument
- `401 Unauthorized` — token present but invalid/expired, or user not found/inactive
- `403 Forbidden` — Bearer credentials absent on a protected route (FastAPI's default `HTTPBearer` behavior), **or** authenticated but lacking the required role
- `404 Not Found` — resource missing or scoped out
- `409 Conflict` — state conflict (e.g. over-budget, report not ready)
- `413 Payload Too Large` — file size exceeds 20 MB cap
- `415 Unsupported Media Type` — file MIME not in allow list
- `422 Unprocessable Entity` — validation error
- `423 Locked` — account lockout active (returns `Retry-After` header)

---

## Health — `/health`

### `GET /health`
Liveness check.
**Auth:** none.
**Response 200:** `{ "status": "ok" }`.

---

## Authentication — `/auth`

### `POST /auth/login`
Exchange username/password for a JWT.
**Body:** `{ "username", "password" }`.
**Response 200:** `{ "access_token", "token_type": "bearer" }`.
**Errors:** 401 (unknown user / deactivated / wrong password), 423 (account lockout — includes `Retry-After` seconds).

> Logout is client-side: the JWT is stateless, so clients drop the token locally. There is no server-side `/auth/logout` route.

---

## Admin — User management — `/admin`

All routes here are **system_admin only**.

### `POST /admin/users`
Create a user.
**Body:** `{ "username", "password", "role" }`.
**Response 201:** `UserResponse`.
**Errors:** 409 (username exists).

### `GET /admin/users`
List users (most recent first).
**Response 200:** `UserResponse[]`.

### `PUT /admin/users/{user_id}/reset-password`
Reset a user's password.
**Body:** `{ "new_password" }`.
**Response 204.**

### `PUT /admin/users/{user_id}/unlock`
Clear a user's lockout window.
**Response 204.**

### `PUT /admin/users/{user_id}/deactivate`
Soft-deactivate a user account.
**Response 204.**

---

## Admin Operations — `/admin`

All routes here are **system_admin only**.

### `GET /admin/backups`
List available backups.
**Response 200:** array of `{ "date": "YYYYMMDD", "db_dump", "db_size_bytes", "has_file_backup" }`.

### `POST /admin/backups/{date}/restore`
Trigger restore from the named backup.
**Path param:** `date` (must match `^\d{8}$`).
**Response 202.**

### `POST /admin/integrity-check`
Re-hash every stored material file and compare against the persisted SHA-256.
**Response 200:** `{ "total": N, "ok": N, "missing": [...], "hash_mismatch": [...], "missing_count": N, "mismatch_count": N }` — each entry in `missing` / `hash_mismatch` carries `version_id`, `material_id`, and `storage_path`; `hash_mismatch` entries additionally include `expected_hash` and `actual_hash`.

### `GET /admin/audit-logs`
Query the audit log.
**Query params:** `user_id?`, `action?` (substring match, case-insensitive), `resource_type?`, `from?` (alias for `from_date`), `to?` (alias for `to_date`), `page?` (default 1), `page_size?` (default 50, max 200).
**Response 200:** `{ "items": [ { "id", "user_id", "action", "resource_type", "resource_id", "details", "ip_address", "user_agent", "created_at" } ], "total": N, "page": N, "page_size": N }`.
**Note:** this endpoint is itself a sensitive read and is audited — a query here writes a `SENSITIVE_READ` row for the acting system admin before returning.

---

## Collection Batches — `/batches`

### `POST /batches`
Create a batch.
**Roles:** system_admin.
**Body:** `{ "name", "submission_deadline", "description?" }`.
**Response 201:** `BatchResponse`.

### `GET /batches`
List batches.
**Response 200:** `BatchResponse[]`.

### `GET /batches/{batch_id}`
Retrieve a batch.

### `PUT /batches/{batch_id}`
Update batch fields.
**Roles:** system_admin.

### `POST /batches/{batch_id}/checklist`
Add a checklist item.
**Roles:** system_admin.
**Body:** `{ "label", "description?", "is_required", "order?" }`.
**Response 201:** `ChecklistItemResponse`.

### `GET /batches/{batch_id}/checklist`
List checklist items for a batch.
**Response 200:** `ChecklistItemResponse[]`.

---

## Registrations — `/registrations`

### `POST /registrations`
Create a draft registration.
**Roles:** applicant.
**Body:** `{ "batch_id", "title?", "activity_type?", "applicant_name?" }`.
**Response 201:** `RegistrationResponse`.

### `PUT /registrations/{registration_id}/draft`
Update draft registration fields (owner only, status must be `draft`).
**Body:** any subset of registration fields plus `wizard_step`.
**Response 200.**

### `POST /registrations/{registration_id}/submit`
Submit a draft for review.
**Response 200:** `RegistrationResponse` with `status == "submitted"`.
**Errors:** 422 (missing required fields or materials).

### `GET /registrations/{registration_id}`
Retrieve a single registration.
**Visibility:** applicants see only their own; reviewers/system_admin see all; reviewers cannot see drafts.

### `GET /registrations`
List registrations visible to the caller.
**Query params:** `page?`, `page_size?`, `status?`, `batch_id?`.
**Response 200:** `{ "items", "total", "page", "page_size" }`.

---

## Materials — `/registrations`

### `POST /registrations/{registration_id}/materials`
Create a material slot linked to a checklist item.
**Query param:** `checklist_item_id`.
**Response 201:** `MaterialResponse`.

### `POST /registrations/{registration_id}/materials/{material_id}/versions`
Upload a new file version.
**Content-Type:** `multipart/form-data`.
**Form field:** `file` (PDF / JPG / PNG, max 20 MB).
**Response 201:** `MaterialVersionResponse` — fields: `id`, `material_id`, `version_number`, `original_filename`, `mime_type`, `file_size_bytes`, `status`, `correction_reason`, `duplicate_flag`, `uploaded_at`.
Internal fingerprint fields (`sha256_hash`, `uploaded_by`) are stored server-side for integrity checks, duplicate detection, and audit logs, and are intentionally **not** exposed through this response.
**Errors:** 413 (> 20 MB), 415 (unsupported MIME), 409 (version cap reached / storage quota).

### `GET /registrations/{registration_id}/materials`
List a registration's materials and versions.
**Response 200:** `MaterialWithVersions[]`.
**Visibility:** reviewers cannot list a draft registration's materials.

### `GET /registrations/versions/{version_id}/download`
Stream a material version file. Every download is a sensitive read and is written to the audit log **before** the bytes are streamed: under `AUDIT_FAIL_CLOSED=1` (default) a failure to persist the audit row blocks the download with a 500; under `AUDIT_FAIL_CLOSED=0` the failure is mirrored to the emergency log and the response carries `X-Audit-Log-Fallback: emergency-log`.
**Errors:** 403 (not permitted to view the registration — e.g. financial_admin, non-owner applicant, reviewer on a draft), 404 (version, material, registration, or file missing on disk), 500 (audit write failed under fail-closed mode).

### `GET /registrations/{registration_id}/materials/upload-info`
Storage quota and supplementary-window eligibility.
**Response 200:** `{ "used_bytes", "limit_bytes", "remaining_bytes", "supplementary_eligible", "supplementary_used" }`.

### `PUT /registrations/versions/{version_id}/status`
Reviewer/admin sets a version's status.
**Body:** `{ "status": "submitted" | "needs_correction", "correction_reason?" }`.
**Errors:** 403 (wrong role or registration not in submitted/supplemented state), 422 (needs_correction without reason).

### `POST /registrations/{registration_id}/supplementary-submit`
Submit supplementary materials during the 72-hour post-deadline window.
**Content-Type:** `multipart/form-data`.
**Form fields:** `correction_reason` (text, required), `material_ids` (repeatable), `files` (repeatable, one-to-one with `material_ids`).
**Response 201:** `MaterialVersionResponse[]`.
**Errors:** 400 (count mismatch between `files` and `material_ids`, empty file list, or duplicate `material_ids`), 403 (outside window, already used, or not the owner), 409 (registration not in an upload-eligible state or version cap reached), 422 (empty `correction_reason` or needs_correction without reason).

---

## Quality Validation — `/registrations`

### `POST /registrations/{registration_id}/validate`
Run all validation rules and persist results.
**Response 200:** `ValidationSummary`.

### `GET /registrations/{registration_id}/validations`
Return the most recent persisted validation results.
**Response 200:** `ValidationSummary`.

---

## Reviews — `/reviews`

### `POST /reviews/registrations/{registration_id}/transition`
Perform a single-registration status transition.
**Roles:** reviewer, system_admin.
**Body:** `{ "to_status", "comment?" }`.
**Response 201:** `ReviewRecordResponse`.
**Errors:** 400 / 409 (illegal transition).

### `POST /reviews/batch`
Batch-review up to 50 registrations in one request.
**Roles:** reviewer, system_admin.
**Body:** `{ "registration_ids": [...≤50...], "action": "approved"|"rejected"|"waitlisted", "comment?" }`.
**Response 200:** `{ "succeeded", "failed", "results": [...] }`.

### `GET /reviews/registrations/{registration_id}/history`
**Response 200:** `ReviewRecordResponse[]`.

### `GET /reviews/registrations/{registration_id}/allowed-transitions`
**Response 200:** `string[]` of status values.

---

## Finance — `/finance`

All routes require **financial_admin** or **system_admin** unless noted.

### `POST /finance/accounts`
Create a funding account.
**Body:** `{ "name", "registration_id", "allocated_budget" }`.
**Response 201:** `FundingAccountResponse`.

### `GET /finance/accounts`
List funding accounts.
**Query params:** `registration_id?`.
**Response 200:** `FundingAccountResponse[]`.

### `GET /finance/accounts/{account_id}`
Account summary with balance, overspending flag, totals.
**Response 200:** `FundingAccountSummary`.

### `POST /finance/accounts/{account_id}/transactions`
Record a transaction.
**Body:** `{ "type": "income"|"expense", "amount", "category", "description?", "over_budget_confirmed?": bool }`.
**Response 201:** `TransactionResponse`.
**Errors:** 409 (would exceed budget; re-submit with `over_budget_confirmed: true` to proceed).

### `GET /finance/accounts/{account_id}/transactions`
List transactions for an account.
**Query params:** `category?`, `from_date?`, `to_date?`.

### `POST /finance/transactions/{transaction_id}/invoice`
Attach an invoice file.
**Content-Type:** `multipart/form-data`.
**Form field:** `file` (PDF / JPG / PNG, max 20 MB).

### `GET /finance/statistics`
Aggregate income/expense totals.
**Query params:** `category?`, `from_date?`, `to_date?`.
**Response 200:** `{ "grand_total_income", "grand_total_expense", "items": [{ "category", "total_income", "total_expense" }] }`.

---

## Metrics & Notifications — top-level

### `GET /metrics`
Operational metrics: counts, rates, and alert-threshold breach flags.
**Roles:** reviewer, system_admin.

### `GET /alert-thresholds`
List configured alert thresholds.
**Roles:** system_admin.

### `PUT /alert-thresholds/{threshold_id}`
Update a threshold.
**Roles:** system_admin.
**Body:** `{ "threshold_value", "comparison" }`.

### `GET /notifications`
List notifications visible to the caller. Applicants see their own; reviewers/admins see global alerts.

### `PUT /notifications/{notification_id}/read`
Mark a notification as read.
**Response 204.**

---

## Reports — `/reports`

All routes require **financial_admin** or **system_admin**. Within that, `audit`, `compliance`, and `whitelist` reports are **system_admin-only** (they expose sensitive access data); financial_admin may only export `reconciliation`.

### `POST /reports/generate/{report_type}`
Generate a report. Creates an `ExportTask` row and a file on disk, so this route is `POST`. Small result sets (≤ 5000 rows) complete synchronously and return the finished task; larger sets run in a background task and the client polls `GET /reports/tasks/{task_id}` until `status == "complete"`.

**Path param:** `report_type` — one of `reconciliation`, `audit`, `compliance`, `whitelist`.
**Query params:** `batch_id?`, `from_date?`, `to_date?`.
**Response 201:** `ExportTaskResponse` (`id`, `report_type`, `status`, `created_by`, `created_at`, `completed_at?`, `error_message?`).
**Errors:** 400 (invalid report type), 403 (role not permitted for this report type).

### `GET /reports/tasks`
List visible export tasks (own tasks for financial_admin; all tasks for system_admin; most recent 50).
**Response 200:** `ExportTaskResponse[]`.

### `GET /reports/tasks/{task_id}`
Retrieve a single task.
**Errors:** 403 (someone else's task), 404.

### `GET /reports/tasks/{task_id}/download`
Download the XLSX file. Every download is written to the audit log **before** the bytes are streamed: under `AUDIT_FAIL_CLOSED=1` (default) a failure to persist the audit row blocks the download with a 500; under `AUDIT_FAIL_CLOSED=0` the failure is mirrored to the emergency log and the response carries `X-Audit-Log-Fallback: emergency-log`.
**Response 200:** `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` attachment.
**Errors:** 403 (not your task), 404 (file missing on disk), 409 (report not ready), 500 (audit write failed under fail-closed mode).

---

## Duplicate detection — `/materials` (optional, feature-flagged)

Registered only when `ENABLE_DUPLICATE_CHECK_API=true`.

### `GET /materials/duplicates`
Find duplicate uploads by SHA-256 hash.
**Roles:** reviewer, system_admin.
**Query params:** `hash` (required).
**Response 200:** `DuplicateMatch[]`.
