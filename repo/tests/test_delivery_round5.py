"""Round-5 regression tests — final acceptance fixes.

Covers:
1. Supplementary submission rejects draft registrations (only 'submitted' allowed).
2. Material version status updates restricted to submitted/supplemented.
3. Magic-byte file validation rejects spoofed content in materials and invoices.
4. .env.example contains all required config variables.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.checklist_item import ChecklistItem
from app.models.collection_batch import CollectionBatch
from app.models.financial import FinancialTransaction, FundingAccount, TransactionType
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token

REPO_ROOT = os.path.join(os.path.dirname(__file__), os.pardir)


# ── Shared helpers ─────────────────────────────────────────────────────────

async def _batch_in_supplementary_window(db_session, admin_user):
    """Batch whose submission deadline has passed but supplementary is open."""
    batch = CollectionBatch(
        name="Supp Window Batch",
        submission_deadline=datetime.now(timezone.utc) - timedelta(hours=1),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    # Set supplementary_deadline far enough in the future.
    batch.supplementary_deadline = datetime.now(timezone.utc) + timedelta(days=2)
    await db_session.commit()
    await db_session.refresh(batch)
    return batch


async def _reg_with_material(
    db_session, applicant_user, batch, status: RegistrationStatus,
):
    ci = ChecklistItem(
        batch_id=batch.id, label="Doc", is_required=True, sort_order=1,
    )
    db_session.add(ci)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=status,
        title="Test",
        activity_type="research",
        description="d",
        applicant_name="A",
    )
    db_session.add(reg)
    await db_session.flush()

    mat = Material(registration_id=reg.id, checklist_item_id=ci.id)
    db_session.add(mat)
    await db_session.flush()

    ver = MaterialVersion(
        material_id=mat.id,
        version_number=1,
        original_filename="doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256_hash="a" * 64,
        storage_path=f"/tmp/fake_{uuid.uuid4().hex}.pdf",
        status=MaterialVersionStatus.SUBMITTED,
        uploaded_by=applicant_user.id,
    )
    db_session.add(ver)
    await db_session.commit()
    for obj in (reg, mat, ver, ci):
        await db_session.refresh(obj)
    return reg, mat, ver, ci


def _supp_form(material_id: uuid.UUID, content: bytes = b"%PDF-1.4 test"):
    """Build multipart form data for a supplementary submit."""
    import io
    return {
        "files": ("supp.pdf", io.BytesIO(content), "application/pdf"),
    }, {
        "correction_reason": "Fixing per reviewer feedback",
        "material_ids": str(material_id),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. Supplementary submission status guards
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_supplementary_rejects_draft_registration(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """A draft registration in the supplementary window must be rejected
    — supplementary is a correction flow for already-submitted work."""
    batch = await _batch_in_supplementary_window(db_session, admin_user)
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.DRAFT,
    )

    import io
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit"
        f"?material_ids={mat.id}",
        files={"files": ("fix.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        data={"correction_reason": "test"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409
    assert "submitted" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_supplementary_accepts_submitted_registration(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """Submitted registration in the supplementary window succeeds."""
    batch = await _batch_in_supplementary_window(db_session, admin_user)
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.SUBMITTED,
    )

    import io
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit"
        f"?material_ids={mat.id}",
        files={"files": ("fix.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        data={"correction_reason": "Addressing feedback"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_supplementary_rejects_already_used(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    """supplementary_used=True must block a second supplementary submit."""
    batch = await _batch_in_supplementary_window(db_session, admin_user)
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.SUBMITTED,
    )
    reg.supplementary_used = True
    await db_session.commit()

    import io
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit"
        f"?material_ids={mat.id}",
        files={"files": ("fix.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        data={"correction_reason": "Second attempt"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409
    assert "already been used" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("finalized_status", [
    RegistrationStatus.APPROVED,
    RegistrationStatus.REJECTED,
    RegistrationStatus.CANCELED,
])
async def test_supplementary_rejects_finalized_statuses(
    client: AsyncClient, applicant_user, admin_user, db_session,
    finalized_status,
):
    """Approved/rejected/canceled registrations cannot use supplementary."""
    batch = await _batch_in_supplementary_window(db_session, admin_user)
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, finalized_status,
    )

    import io
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/supplementary-submit"
        f"?material_ids={mat.id}",
        files={"files": ("fix.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        data={"correction_reason": "Nope"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# 2. Material version status update guards
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_material_status_update_works_on_submitted(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session,
):
    batch = CollectionBatch(
        name="MSU Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.SUBMITTED,
    )

    resp = await client.put(
        f"/api/v1/registrations/versions/{ver.id}/status",
        json={"status": "needs_correction", "correction_reason": "Blurry scan"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "needs_correction"


@pytest.mark.asyncio
async def test_material_status_update_works_on_supplemented(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session,
):
    batch = CollectionBatch(
        name="MSU Batch2",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.SUPPLEMENTED,
    )

    resp = await client.put(
        f"/api/v1/registrations/versions/{ver.id}/status",
        json={"status": "submitted"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", [
    RegistrationStatus.DRAFT,
    RegistrationStatus.APPROVED,
    RegistrationStatus.REJECTED,
    RegistrationStatus.WAITLISTED,
    RegistrationStatus.PROMOTED_FROM_WAITLIST,
    RegistrationStatus.CANCELED,
])
async def test_material_status_update_rejected_for_non_active_states(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session,
    bad_status,
):
    """Material version status cannot be changed when the registration is
    not in an active-review state."""
    batch = CollectionBatch(
        name="MSU Bad",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, bad_status,
    )

    resp = await client.put(
        f"/api/v1/registrations/versions/{ver.id}/status",
        json={"status": "needs_correction", "correction_reason": "Test"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_applicant_cannot_call_material_status_update(
    client: AsyncClient, applicant_user, admin_user, db_session,
):
    batch = CollectionBatch(
        name="Applicant block",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()
    reg, mat, ver, ci = await _reg_with_material(
        db_session, applicant_user, batch, RegistrationStatus.SUBMITTED,
    )

    resp = await client.put(
        f"/api/v1/registrations/versions/{ver.id}/status",
        json={"status": "submitted"},
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 3. Magic-byte file validation
# ═══════════════════════════════════════════════════════════════════════════

def test_validate_file_content_accepts_real_pdf():
    from app.utils.file_validation import validate_file_content
    validate_file_content(b"%PDF-1.4 real content", "application/pdf")


def test_validate_file_content_accepts_real_jpeg():
    from app.utils.file_validation import validate_file_content
    validate_file_content(b"\xff\xd8\xff\xe0 JFIF data", "image/jpeg")


def test_validate_file_content_accepts_real_png():
    from app.utils.file_validation import validate_file_content
    validate_file_content(b"\x89PNG\r\n\x1a\n more data", "image/png")


def test_validate_file_content_rejects_spoofed_pdf():
    from app.utils.file_validation import validate_file_content
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_file_content(b"not a pdf at all", "application/pdf")
    assert exc.value.status_code == 415
    assert "does not match" in exc.value.detail


def test_validate_file_content_rejects_spoofed_png():
    from app.utils.file_validation import validate_file_content
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_file_content(b"plain text pretending to be png", "image/png")
    assert exc.value.status_code == 415


def test_validate_file_content_rejects_unknown_mime():
    from app.utils.file_validation import validate_file_content
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        validate_file_content(b"%PDF-1.4 real content", "application/octet-stream")
    assert exc.value.status_code == 415
    assert "not allowed" in exc.value.detail


# ═══════════════════════════════════════════════════════════════════════════
# 4. .env.example completeness
# ═══════════════════════════════════════════════════════════════════════════

def test_env_example_exists_and_covers_config():
    """`.env.example` must exist and contain all key config variables from
    ``app.config.Settings`` so a fresh operator has a complete template."""
    import pathlib
    env_path = pathlib.Path(__file__).resolve().parent.parent / ".env.example"
    assert env_path.is_file(), ".env.example must exist at the repo root"

    content = env_path.read_text()
    required = [
        "DATABASE_URL",
        "SECRET_KEY",
        "SENSITIVE_FIELD_KEY",
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "ENABLE_DUPLICATE_CHECK_API",
        "LOCKOUT_ATTEMPT_LIMIT",
        "LOCKOUT_WINDOW_MINUTES",
        "LOCKOUT_DURATION_MINUTES",
        "AUDIT_FAIL_CLOSED",
        "DECRYPT_FAIL_CLOSED",
        "VALIDATION_FAIL_CLOSED",
        "ALERT_FAIL_CLOSED",
    ]
    for var in required:
        assert var in content, f".env.example missing {var}"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Static structural guards for the new code
# ═══════════════════════════════════════════════════════════════════════════

def test_materials_uses_file_validation():
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app" / "api" / "v1" / "materials.py"
    ).read_text()
    assert "from app.utils.file_validation import validate_file_content" in src
    assert "validate_file_content(" in src


def test_finance_uses_file_validation():
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app" / "api" / "v1" / "finance.py"
    ).read_text()
    assert "from app.utils.file_validation import validate_file_content" in src
    assert "validate_file_content(" in src


def test_supplementary_rejects_draft_in_source():
    """Structural guard: _get_uploadable_registration must NOT include DRAFT
    in the supplementary branch's allowed-status set."""
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app" / "api" / "v1" / "materials.py"
    ).read_text()
    assert "reg.status != RegistrationStatus.SUBMITTED" in src or \
           "only available for registrations" in src


def test_material_status_update_uses_strict_set():
    """Structural guard: update_material_version_status must use a strict
    allowlist, not a 'not DRAFT' check."""
    import pathlib
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app" / "api" / "v1" / "materials.py"
    ).read_text()
    assert "_ACTIVE_REVIEW_STATUSES" in src
    assert "SUBMITTED" in src and "SUPPLEMENTED" in src
