"""Tests for material upload, versioning, status updates, and authorization."""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection_batch import CollectionBatch
from app.models.checklist_item import ChecklistItem
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from tests.conftest import make_token


async def _setup_registration(db_session, applicant_user, admin_user, status=RegistrationStatus.SUBMITTED):
    batch = CollectionBatch(
        name="Mat Test Batch",
        submission_deadline=datetime.now(timezone.utc) + timedelta(days=30),
        created_by=admin_user.id,
    )
    db_session.add(batch)
    await db_session.flush()

    checklist_item = ChecklistItem(
        batch_id=batch.id,
        label="Test Document",
        is_required=True,
    )
    db_session.add(checklist_item)
    await db_session.flush()

    reg = Registration(
        batch_id=batch.id,
        applicant_id=applicant_user.id,
        status=status,
        title="Mat Test",
        activity_type="research",
        description="Desc",
        applicant_name="Tester",
    )
    db_session.add(reg)
    await db_session.flush()

    material = Material(
        registration_id=reg.id,
        checklist_item_id=checklist_item.id,
    )
    db_session.add(material)
    await db_session.commit()
    await db_session.refresh(material)
    await db_session.refresh(reg)
    return reg, material, checklist_item


def _pdf_file(name="test.pdf", size=1024):
    """Return a small fake PDF upload."""
    return ("file", (name, io.BytesIO(b"%PDF-1.4 " + b"0" * size), "application/pdf"))


@pytest.mark.asyncio
async def test_reviewer_can_update_submitted_material_status(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    """Reviewer can set material version status on a submitted registration."""
    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)

    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256_hash="abc123",
        storage_path="/tmp/test.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    headers = make_token(reviewer_user)
    resp = await client.put(
        f"/api/v1/registrations/versions/{version.id}/status",
        json={"status": "submitted"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_reviewer_blocked_from_draft_material_status_update(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    """Reviewer must not update material status on a draft registration."""
    reg, material, _ = await _setup_registration(
        db_session, applicant_user, admin_user, status=RegistrationStatus.DRAFT
    )

    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        original_filename="draft.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256_hash="def456",
        storage_path="/tmp/draft.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    headers = make_token(reviewer_user)
    resp = await client.put(
        f"/api/v1/registrations/versions/{version.id}/status",
        json={"status": "submitted"},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_needs_correction_requires_reason(
    client: AsyncClient, reviewer_user, applicant_user, admin_user, db_session
):
    """Setting needs_correction without a reason must be rejected."""
    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)

    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        original_filename="test2.pdf",
        mime_type="application/pdf",
        file_size_bytes=512,
        sha256_hash="ghi789",
        storage_path="/tmp/test2.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    headers = make_token(reviewer_user)
    resp = await client.put(
        f"/api/v1/registrations/versions/{version.id}/status",
        json={"status": "needs_correction"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_material_status_update_requires_reviewer_role(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Applicant must not be able to update material version status."""
    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)

    version = MaterialVersion(
        material_id=material.id,
        version_number=1,
        original_filename="test3.pdf",
        mime_type="application/pdf",
        file_size_bytes=512,
        sha256_hash="jkl012",
        storage_path="/tmp/test3.pdf",
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        uploaded_by=applicant_user.id,
    )
    db_session.add(version)
    await db_session.commit()
    await db_session.refresh(version)

    headers = make_token(applicant_user)
    resp = await client.put(
        f"/api/v1/registrations/versions/{version.id}/status",
        json={"status": "submitted"},
        headers=headers,
    )
    assert resp.status_code == 403


# ── Path-traversal adversarial upload tests ────────────────────────────────

@pytest.mark.asyncio
async def test_upload_with_path_traversal_filename_is_safe(
    client: AsyncClient, applicant_user, admin_user, db_session, tmp_path, monkeypatch
):
    """Uploading a file with a path-traversal filename must not escape storage root.

    The server must generate its own safe filename (UUID-based) regardless of
    what the client sends as `filename`. The stored path must stay under the
    intended storage directory.
    """
    import app.api.v1.materials as mat_module

    # Point storage root to a temp dir so the test doesn't touch /storage
    monkeypatch.setattr(mat_module, "_STORAGE_ROOT", str(tmp_path))

    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)
    headers = make_token(applicant_user)

    adversarial_names = [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "/absolute/path/evil.pdf",
        "normal_name/../../etc/shadow",
        "\x00nullbyte.pdf",
    ]

    for evil_name in adversarial_names:
        payload = io.BytesIO(b"%PDF-1.4 " + b"x" * 512)
        resp = await client.post(
            f"/api/v1/registrations/{reg.id}/materials/{material.id}/versions",
            files={"file": (evil_name, payload, "application/pdf")},
            headers=headers,
        )
        # Must succeed (upload is valid PDF) or 409 (version cap) — never a 5xx
        # Security is enforced by _safe_storage_filename (UUID-based) and
        # _assert_path_under_root in the server code, not by rejection of the name.
        assert resp.status_code in (201, 409), (
            f"Unexpected status {resp.status_code} for filename '{evil_name}'"
        )
        if resp.status_code == 201:
            # Verify all files written by the server are inside the storage root
            # and have safe (UUID-based, no traversal) filenames.
            import os
            for fname in os.listdir(str(tmp_path)):
                assert ".." not in fname, (
                    f"Path traversal in stored filename for '{evil_name}': {fname}"
                )


@pytest.mark.asyncio
async def test_upload_disallowed_mime_type_rejected(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Uploading a disallowed MIME type must return 415."""
    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)
    headers = make_token(applicant_user)

    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials/{material.id}/versions",
        files={"file": ("evil.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
        headers=headers,
    )
    assert resp.status_code == 415, f"Expected 415 for disallowed MIME, got {resp.status_code}"


@pytest.mark.asyncio
async def test_upload_oversized_file_rejected(
    client: AsyncClient, applicant_user, admin_user, db_session
):
    """Uploading a file larger than 20 MB must return 413."""
    reg, material, _ = await _setup_registration(db_session, applicant_user, admin_user)
    headers = make_token(applicant_user)

    # 21 MB of data
    big_content = b"%PDF-1.4 " + b"0" * (21 * 1024 * 1024)
    resp = await client.post(
        f"/api/v1/registrations/{reg.id}/materials/{material.id}/versions",
        files={"file": ("big.pdf", io.BytesIO(big_content), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 413, f"Expected 413 for oversized file, got {resp.status_code}"
