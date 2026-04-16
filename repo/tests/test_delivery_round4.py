"""Round-4 regression tests for the last remaining audit findings.

Issues addressed:

1. **Blocker** — the runtime image did not ship ``pg_restore`` or
   ``rsync``, so one-click restore was un-deliverable despite the API
   and README advertising it. Pinned by a Dockerfile structural test.
2. **High** — registration create/draft-update/submit never enforced
   the batch submission deadline, so late writes slipped through even
   though material uploads were already deadline-locked. Pinned by
   integration tests.
3. **Medium** — supplementary UI only validated per-file size; the
   aggregate 200 MB cap was enforced server-side only. Pinned by a
   frontend vitest case in the companion spec file.
4. **Low** — sensitive-read audit fallback never reached the client as
   a real header. Pinned by a structural test that the audit
   middleware forwards ``request.state.audit_read_fallback`` to a
   response header.
"""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models.collection_batch import CollectionBatch
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── Blocker: restore tooling baked into the image ──────────────────────────

def test_dockerfile_installs_restore_tooling():
    """``postgresql-client`` (for pg_restore) and ``rsync`` must be
    installed into the runtime image; otherwise POST
    /api/v1/admin/backups/{date}/restore cannot execute its subprocess
    calls. The audit previously flagged this as a Blocker."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "postgresql-client" in dockerfile, (
        "Dockerfile must install a postgresql-client package so "
        "pg_restore is available to the restore endpoint."
    )
    assert "rsync" in dockerfile, (
        "Dockerfile must install rsync so the restore endpoint can "
        "copy material/invoice files back into /storage."
    )
    assert "apt.postgresql.org" in dockerfile, (
        "Dockerfile must pull the PG client from the pgdg repo so the "
        "client major matches PG 16 in docker-compose.yml."
    )


def test_entrypoint_exists_and_runs_age_on_encrypted_env():
    """The runtime entrypoint must integrate age-based decryption of
    ``.env.age`` so the Prompt's 'encryption of sensitive configurations'
    requirement is met by the actual runtime, not just by an offline
    operator script."""
    entry = (REPO_ROOT / "scripts" / "entrypoint.sh").read_text()
    assert "age --decrypt" in entry
    assert "ENV_AGE_KEY_FILE" in entry
    assert "ENV_AGE_PASSPHRASE" in entry
    # Trap ensures the plaintext does not outlive the container.
    assert "trap" in entry and "rm -f" in entry


def test_dockerfile_wires_entrypoint():
    """The Dockerfile must set the entrypoint so age decryption runs
    before uvicorn. Without this hop, the encrypted-config support is
    inert even though the binary and helper script exist."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in dockerfile


# ── High: registration deadline enforcement ────────────────────────────────

async def _batch_with_deadline(db_session, admin_user, *, offset_days: float, sup_offset_days: float | None = None):
    sd = datetime.now(timezone.utc) + timedelta(days=offset_days)
    kwargs = dict(
        name="Deadline Batch",
        submission_deadline=sd,
        created_by=admin_user.id,
    )
    batch = CollectionBatch(**kwargs)
    db_session.add(batch)
    await db_session.flush()
    if sup_offset_days is not None:
        # The model has a computed supplementary_deadline in PG; overriding
        # would not normally persist. For the test we insert directly and
        # rely on the fact that ``_assert_batch_window`` reads the column
        # as-is. In the ORM test schema (create_all), the computed column
        # is a plain column, which lets us set it explicitly.
        batch.supplementary_deadline = sd + timedelta(days=sup_offset_days - offset_days)
    await db_session.commit()
    await db_session.refresh(batch)
    return batch


@pytest.mark.asyncio
async def test_registration_create_rejected_after_deadline(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """Creating a new registration against a batch whose submission
    deadline has passed must be rejected with 403. Mirrors the
    material-upload deadline policy."""
    batch = await _batch_with_deadline(
        db_session, admin_user,
        offset_days=-1,       # submission closed 1 day ago
        sup_offset_days=-0.5, # supplementary also closed
    )
    resp = await client.post(
        "/api/v1/registrations",
        json={"batch_id": str(batch.id), "title": "Late draft"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "submission window is closed" in detail or "submission deadline has passed" in detail


@pytest.mark.asyncio
async def test_registration_draft_update_rejected_after_deadline(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """Editing a draft is disallowed once the batch deadline has
    passed, even if the draft itself was created on time."""
    # Start with a still-open deadline so we can create a draft.
    batch = await _batch_with_deadline(
        db_session, admin_user, offset_days=1, sup_offset_days=2,
    )
    create_resp = await client.post(
        "/api/v1/registrations",
        json={"batch_id": str(batch.id), "title": "Timely draft"},
        headers=make_token(applicant_user),
    )
    assert create_resp.status_code == 201
    reg_id = create_resp.json()["id"]

    # Move deadlines into the past.
    batch.submission_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    batch.supplementary_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/registrations/{reg_id}/draft",
        json={"title": "Sneaky late edit"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_registration_submit_rejected_after_deadline(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """A draft cannot be submitted once the batch deadline has closed."""
    batch = await _batch_with_deadline(
        db_session, admin_user, offset_days=1, sup_offset_days=2,
    )
    create_resp = await client.post(
        "/api/v1/registrations",
        json={"batch_id": str(batch.id), "title": "Will miss deadline"},
        headers=make_token(applicant_user),
    )
    reg_id = create_resp.json()["id"]

    # Backdate the deadlines entirely.
    batch.submission_deadline = datetime.now(timezone.utc) - timedelta(days=1)
    batch.supplementary_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/registrations/{reg_id}/submit",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_registration_create_in_supplementary_window_is_rejected(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """Between the submission deadline and the supplementary deadline,
    new-draft creation is disallowed — the supplementary endpoint is
    the only path forward and it does not cover fresh drafts."""
    batch = await _batch_with_deadline(
        db_session, admin_user,
        offset_days=-0.5,      # submission closed 12 hours ago
        sup_offset_days=2,     # supplementary still open
    )
    resp = await client.post(
        "/api/v1/registrations",
        json={"batch_id": str(batch.id), "title": "Too late"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


# ── Low: read-audit fallback header propagation ────────────────────────────

def test_audit_middleware_forwards_read_fallback_header():
    """The audit middleware must copy ``request.state.audit_read_fallback``
    onto the response as ``X-Audit-Log-Fallback``. Without this hop,
    the ``audit_read`` dependency's fallback signal never reaches the
    client, falsifying the comment/contract on the read-audit module."""
    import inspect
    from app.middleware.audit import AuditMiddleware
    src = inspect.getsource(AuditMiddleware.dispatch)
    assert "audit_read_fallback" in src, (
        "AuditMiddleware.dispatch must read request.state.audit_read_fallback"
    )
    assert "X-Audit-Log-Fallback" in src, (
        "AuditMiddleware.dispatch must set the X-Audit-Log-Fallback header"
    )


# ── Medium: supplementary aggregate-size validation (structural) ──────────

def test_supplementary_ui_computes_aggregate_size():
    """Structural guard: the supplementary file-select handler must
    compute the projected aggregate size against ``uploadInfo.limit_bytes``
    in addition to the per-file cap. The previous version of the
    handler only checked per-file size, which did not meet the
    Prompt's 'real-time total upload limit' requirement."""
    sfc = (
        REPO_ROOT / "frontend" / "src" / "views" / "RegistrationWizardPage.vue"
    ).read_text()
    assert "handleSuppFileSelect" in sfc
    assert "limit_bytes" in sfc, (
        "supplementary handler must reference uploadInfo.limit_bytes"
    )
    assert "Supplementary upload would exceed" in sfc, (
        "user-facing error for the aggregate-size breach is missing"
    )


def test_restore_endpoint_handles_new_backup_layout():
    """Structural guard: the restore handler must understand the new
    materials/ + invoices/ backup layout so one-click recovery is
    self-consistent with the backup script's output."""
    src = (REPO_ROOT / "app" / "api" / "v1" / "admin_ops.py").read_text()
    assert "materials_src" in src and "invoices_src" in src
    assert "invoices_attempted" in src
    assert "db_ok and files_ok and invoices_ok" in src
