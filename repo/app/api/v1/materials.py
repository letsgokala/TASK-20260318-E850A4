import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.auth.read_audit import audit_read
from app.database import get_db
from app.models.audit_log import AuditLog
from app.utils.emergency_log import record_critical_failure
from app.utils.file_validation import validate_file_content

logger = logging.getLogger(__name__)
from app.models.collection_batch import CollectionBatch
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.schemas.material import (
    MaterialResponse,
    MaterialStatusUpdate,
    MaterialVersionResponse,
    MaterialWithVersions,
    UploadSizeInfo,
)

router = APIRouter()

_ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
_MAX_TOTAL_SIZE = 200 * 1024 * 1024  # 200 MB
_MAX_VERSIONS = 3
_STORAGE_ROOT = "/storage/materials"


def _safe_storage_filename(original_filename: str | None, mime_type: str | None = None) -> str:
    """Return a safe, server-generated filename.

    The original filename is stored in the DB for display purposes; the on-disk
    name is a random UUID with a whitelisted extension to prevent path traversal,
    null-byte injection, and reserved-name attacks.
    """
    # Derive extension from the original name (basename only, never trust full path)
    ext = ""
    if original_filename:
        base = os.path.basename(original_filename)  # strip any directory component
        _, raw_ext = os.path.splitext(base)
        if raw_ext.lower() in _ALLOWED_EXTENSIONS:
            ext = raw_ext.lower()

    # Fall back to MIME-derived extension if original gave nothing usable
    if not ext and mime_type:
        _mime_ext_map = {
            "application/pdf": ".pdf",
            "image/jpeg": ".jpg",
            "image/png": ".png",
        }
        ext = _mime_ext_map.get(mime_type, "")

    return f"{uuid.uuid4().hex}{ext}"


def _assert_path_under_root(resolved_path: str, root: str) -> None:
    """Raise if resolved_path escapes the intended storage root (defence-in-depth)."""
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(resolved_path)
    if not real_path.startswith(real_root + os.sep) and real_path != real_root:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid upload path.",
        )


# ── Upload a new version ───────────────────────────────────────────────────

@router.post(
    "/{registration_id}/materials/{material_id}/versions",
    response_model=MaterialVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material_version(
    registration_id: uuid.UUID,
    material_id: uuid.UUID,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = await _get_uploadable_registration(registration_id, current_user, db)

    # Verify material belongs to this registration
    mat_result = await db.execute(
        select(Material).where(Material.id == material_id, Material.registration_id == reg.id)
    )
    material = mat_result.scalar_one_or_none()
    if not material:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    # Read file content first so we can validate both MIME and magic bytes.
    content = await file.read()
    file_size = len(content)

    # Validate declared MIME + magic-byte signature (defence-in-depth).
    validate_file_content(content, file.content_type, context="material")

    if file_size > _MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {_MAX_FILE_SIZE // (1024*1024)}MB",
        )

    # Check total size for registration
    total_result = await db.execute(
        select(func.coalesce(func.sum(MaterialVersion.file_size_bytes), 0))
        .select_from(MaterialVersion)
        .join(Material, MaterialVersion.material_id == Material.id)
        .where(Material.registration_id == reg.id)
    )
    current_total = total_result.scalar_one()

    if current_total + file_size > _MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Total upload size would exceed {_MAX_TOTAL_SIZE // (1024*1024)}MB limit",
        )

    # Check version count
    version_count_result = await db.execute(
        select(func.count())
        .select_from(MaterialVersion)
        .where(MaterialVersion.material_id == material.id)
    )
    version_count = version_count_result.scalar_one()

    if version_count >= _MAX_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Maximum of {_MAX_VERSIONS} versions reached for this material",
        )

    new_version_number = version_count + 1
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Store file on disk
    storage_dir = os.path.join(
        _STORAGE_ROOT, str(reg.id), str(material.id), f"v{new_version_number}"
    )
    os.makedirs(storage_dir, exist_ok=True)
    safe_filename = _safe_storage_filename(file.filename, file.content_type)
    storage_path = os.path.join(storage_dir, safe_filename)
    _assert_path_under_root(storage_path, _STORAGE_ROOT)

    with open(storage_path, "wb") as f:
        f.write(content)

    # Check for cross-registration duplicates
    dup_result = await db.execute(
        select(MaterialVersion.id)
        .where(MaterialVersion.sha256_hash == sha256_hash)
    )
    existing_dup = dup_result.scalars().first()

    version = MaterialVersion(
        material_id=material.id,
        version_number=new_version_number,
        original_filename=file.filename or "upload",
        mime_type=file.content_type,
        file_size_bytes=file_size,
        sha256_hash=sha256_hash,
        storage_path=storage_path,
        status=MaterialVersionStatus.PENDING_SUBMISSION,
        duplicate_flag=existing_dup is not None,
        duplicate_of=existing_dup,
        uploaded_by=current_user.id,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return version


# ── Create material (link checklist item to registration) ──────────────────

@router.post(
    "/{registration_id}/materials",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material(
    registration_id: uuid.UUID,
    checklist_item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = await _get_uploadable_registration(registration_id, current_user, db)

    # Verify checklist item belongs to the registration's batch
    from app.models.checklist_item import ChecklistItem
    ci_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.id == checklist_item_id)
    )
    checklist_item = ci_result.scalar_one_or_none()
    if not checklist_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checklist item not found",
        )
    if checklist_item.batch_id != reg.batch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Checklist item does not belong to this registration's batch",
        )

    # Check if material already exists for this checklist item
    existing = await db.execute(
        select(Material).where(
            Material.registration_id == reg.id,
            Material.checklist_item_id == checklist_item_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Material for this checklist item already exists",
        )

    material = Material(
        registration_id=reg.id,
        checklist_item_id=checklist_item_id,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


# ── List materials for a registration ──────────────────────────────────────

@router.get(
    "/{registration_id}/materials",
    response_model=list[MaterialWithVersions],
)
async def list_materials(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _audit: None = Depends(audit_read("material", "list", "registration_id")),
):
    await _get_visible_registration(registration_id, current_user, db)

    mat_result = await db.execute(
        select(Material)
        .where(Material.registration_id == registration_id)
        .order_by(Material.created_at)
    )
    materials = mat_result.scalars().all()

    response = []
    for mat in materials:
        ver_result = await db.execute(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == mat.id)
            .order_by(MaterialVersion.version_number)
        )
        versions = ver_result.scalars().all()
        response.append(MaterialWithVersions(
            id=mat.id,
            registration_id=mat.registration_id,
            checklist_item_id=mat.checklist_item_id,
            created_at=mat.created_at,
            versions=[MaterialVersionResponse.model_validate(v) for v in versions],
        ))
    return response


# ── Download a material version ────────────────────────────────────────────

@router.get(
    "/versions/{version_id}/download",
)
async def download_material_version(
    version_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ver_result = await db.execute(
        select(MaterialVersion).where(MaterialVersion.id == version_id)
    )
    version = ver_result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    # Verify access through registration
    mat_result = await db.execute(select(Material).where(Material.id == version.material_id))
    material = mat_result.scalar_one_or_none()
    if not material:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    await _get_visible_registration(material.registration_id, current_user, db)

    if not os.path.isfile(version.storage_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File missing on disk",
        )

    # Audit: every material download is a sensitive read and must be logged
    # BEFORE the file is streamed. The audit report previously flagged this
    # endpoint as fail-open (``except Exception: pass``), which allowed
    # unaudited access to sensitive files. The policy is now:
    #   - AUDIT_FAIL_CLOSED=1 (default): audit write failure blocks the
    #     download with a 500 so no sensitive bytes leave the server
    #     without a matching audit row.
    #   - AUDIT_FAIL_CLOSED=0: the failure is mirrored to the emergency log
    #     and the download proceeds with an X-Audit-Log-Fallback header.
    audit_entry = AuditLog(
        user_id=current_user.id,
        action=f"DOWNLOAD material_version/{version_id}",
        resource_type="material_version",
        resource_id=version_id,
        details={
            "original_filename": version.original_filename,
            "material_id": str(version.material_id),
        },
        ip_address=(
            request.client.host if request and request.client else None
        ),
        user_agent=(
            request.headers.get("user-agent") if request else None
        ),
    )
    db.add(audit_entry)
    fallback_header: dict[str, str] = {}
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(
            "Material download audit write failed",
            extra={"version_id": str(version_id), "user_id": str(current_user.id)},
            exc_info=True,
        )
        record_critical_failure(
            category="audit_download_material",
            message="failed to persist download audit row",
            version_id=str(version_id),
            user_id=str(current_user.id),
            error=repr(exc),
        )
        from app.config import settings as _settings
        if _settings.AUDIT_FAIL_CLOSED == "1":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Audit log persistence failed; download blocked "
                    "(AUDIT_FAIL_CLOSED=1)."
                ),
            ) from exc
        fallback_header = {"X-Audit-Log-Fallback": "emergency-log"}

    return FileResponse(
        version.storage_path,
        filename=version.original_filename,
        media_type=version.mime_type,
        headers=fallback_header or None,
    )


# ── Upload size info ───────────────────────────────────────────────────────

@router.get(
    "/{registration_id}/materials/upload-info",
    response_model=UploadSizeInfo,
)
async def get_upload_size_info(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = await _get_visible_registration(registration_id, current_user, db)

    total_result = await db.execute(
        select(func.coalesce(func.sum(MaterialVersion.file_size_bytes), 0))
        .select_from(MaterialVersion)
        .join(Material, MaterialVersion.material_id == Material.id)
        .where(Material.registration_id == registration_id)
    )
    used = total_result.scalar_one()

    # Compute supplementary eligibility from batch deadlines
    batch_result = await db.execute(
        select(CollectionBatch).where(CollectionBatch.id == reg.batch_id)
    )
    batch = batch_result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    supplementary_eligible = False
    if batch and reg.status == RegistrationStatus.SUBMITTED and not reg.supplementary_used:
        # Normalise naive datetimes from SQLite to UTC for comparison.
        sub_dl = batch.submission_deadline
        if sub_dl is not None and sub_dl.tzinfo is None:
            sub_dl = sub_dl.replace(tzinfo=timezone.utc)
        sup_dl = batch.supplementary_deadline
        if sup_dl is not None and sup_dl.tzinfo is None:
            sup_dl = sup_dl.replace(tzinfo=timezone.utc)
        supplementary_eligible = (
            sub_dl is not None
            and sup_dl is not None
            and now > sub_dl
            and now <= sup_dl
        )

    return UploadSizeInfo(
        used_bytes=used,
        limit_bytes=_MAX_TOTAL_SIZE,
        remaining_bytes=max(0, _MAX_TOTAL_SIZE - used),
        supplementary_eligible=supplementary_eligible,
        supplementary_used=reg.supplementary_used,
    )


# ── Material version status update (reviewer correction workflow) ──────────

_reviewer_or_admin = require_roles(UserRole.REVIEWER, UserRole.SYSTEM_ADMIN)


@router.put(
    "/versions/{version_id}/status",
    response_model=MaterialVersionResponse,
)
async def update_material_version_status(
    version_id: uuid.UUID,
    body: MaterialStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_reviewer_or_admin),
):
    """Reviewer sets a material version to 'submitted', 'needs_correction', etc.

    When setting needs_correction, a correction_reason is required.
    """
    ver_result = await db.execute(
        select(MaterialVersion).where(MaterialVersion.id == version_id)
    )
    version = ver_result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    # Resolve to registration and enforce visibility — reviewers cannot touch draft materials
    mat_check = await db.execute(select(Material).where(Material.id == version.material_id))
    owning_material = mat_check.scalar_one_or_none()
    if not owning_material:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent material not found")
    reg_check = await db.execute(
        select(Registration).where(Registration.id == owning_material.registration_id)
    )
    owning_reg = reg_check.scalar_one_or_none()
    # Reviewer corrections are only meaningful while the registration is
    # under active review. Once it reaches a terminal or waitlisted state,
    # material-level status changes must be rejected so the review record
    # cannot drift after the decision is locked. The audit flagged the
    # prior "not draft" check as too permissive.
    _ACTIVE_REVIEW_STATUSES = {
        RegistrationStatus.SUBMITTED,
        RegistrationStatus.SUPPLEMENTED,
    }
    if not owning_reg or owning_reg.status not in _ACTIVE_REVIEW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Cannot modify material status: the owning registration must be in "
                f"'submitted' or 'supplemented' status (current: "
                f"'{owning_reg.status.value if owning_reg else 'not found'}')."
            ),
        )

    if body.status == MaterialVersionStatus.NEEDS_CORRECTION and not body.correction_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="correction_reason is required when setting status to needs_correction",
        )

    version.status = body.status
    version.correction_reason = body.correction_reason
    await db.commit()
    await db.refresh(version)
    return version


# ── Supplementary submission (one-time batch upload) ───────────────────────

@router.post(
    "/{registration_id}/supplementary-submit",
    response_model=list[MaterialVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def supplementary_submit(
    registration_id: uuid.UUID,
    files: list[UploadFile],
    material_ids: list[uuid.UUID],
    correction_reason: str = Form("", description="Applicant's explanation for the supplementary submission"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One-time supplementary submission within 72 hours after deadline.

    Accepts multiple files paired with their material_ids (parallel lists).
    correction_reason records the applicant's rationale and is stored on every
    uploaded version for traceability.
    Sets supplementary_used=True and transitions status to 'supplemented'.
    """
    if not correction_reason or not correction_reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="correction_reason is required for supplementary submissions",
        )
    correction_reason = correction_reason.strip()
    if len(files) != len(material_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Number of files must match number of material_ids",
        )
    if len(files) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )
    if len(material_ids) != len(set(material_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate material_ids are not allowed",
        )

    # Row-level lock to prevent concurrent supplementary submissions
    result = await db.execute(
        select(Registration)
        .where(Registration.id == registration_id)
        .with_for_update()
    )
    reg_locked = result.scalar_one_or_none()
    if not reg_locked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    reg = await _get_uploadable_registration(
        registration_id, current_user, db, is_supplementary=True
    )

    # Check total size budget
    total_result = await db.execute(
        select(func.coalesce(func.sum(MaterialVersion.file_size_bytes), 0))
        .select_from(MaterialVersion)
        .join(Material, MaterialVersion.material_id == Material.id)
        .where(Material.registration_id == reg.id)
    )
    current_total = total_result.scalar_one()

    created_versions: list[MaterialVersion] = []

    for file, mid in zip(files, material_ids):
        # Validate material ownership
        mat_result = await db.execute(
            select(Material).where(Material.id == mid, Material.registration_id == reg.id)
        )
        material = mat_result.scalar_one_or_none()
        if not material:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Material {mid} not found in this registration",
            )

        content = await file.read()
        file_size = len(content)

        # Validate declared MIME + magic-byte signature.
        validate_file_content(content, file.content_type, context="material")

        if file_size > _MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{file.filename}' exceeds {_MAX_FILE_SIZE // (1024*1024)}MB",
            )

        current_total += file_size
        if current_total > _MAX_TOTAL_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Total upload size would exceed {_MAX_TOTAL_SIZE // (1024*1024)}MB limit",
            )

        # Version count check
        vc_result = await db.execute(
            select(func.count()).select_from(MaterialVersion)
            .where(MaterialVersion.material_id == material.id)
        )
        version_count = vc_result.scalar_one()
        if version_count >= _MAX_VERSIONS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Material {mid} already has {_MAX_VERSIONS} versions",
            )

        new_ver = version_count + 1
        sha256_hash = hashlib.sha256(content).hexdigest()

        storage_dir = os.path.join(
            _STORAGE_ROOT, str(reg.id), str(material.id), f"v{new_ver}"
        )
        os.makedirs(storage_dir, exist_ok=True)
        safe_filename = _safe_storage_filename(file.filename, file.content_type)
        storage_path = os.path.join(storage_dir, safe_filename)
        _assert_path_under_root(storage_path, _STORAGE_ROOT)
        with open(storage_path, "wb") as f:
            f.write(content)

        dup_result = await db.execute(
            select(MaterialVersion.id).where(MaterialVersion.sha256_hash == sha256_hash)
        )
        existing_dup = dup_result.scalars().first()

        # Supplementary uploads advance the lifecycle to submitted
        # immediately — the applicant has explicitly attested the
        # correction, so there is no interstitial pending state to
        # maintain for reviewer visibility. Created alongside the
        # registration transition in the same transaction.
        version = MaterialVersion(
            material_id=material.id,
            version_number=new_ver,
            original_filename=file.filename or "upload",
            mime_type=file.content_type,
            file_size_bytes=file_size,
            sha256_hash=sha256_hash,
            storage_path=storage_path,
            status=MaterialVersionStatus.SUBMITTED,
            correction_reason=correction_reason,
            duplicate_flag=existing_dup is not None,
            duplicate_of=existing_dup,
            uploaded_by=current_user.id,
        )
        db.add(version)
        created_versions.append(version)

    # Advance any pending_submission versions still attached to this
    # registration (e.g. pre-supplementary uploads that were never
    # promoted by the initial submit path) so the entire material set
    # lands in a post-submit state after this transaction commits.
    from sqlalchemy import update as _update
    await db.execute(
        _update(MaterialVersion)
        .where(
            MaterialVersion.status == MaterialVersionStatus.PENDING_SUBMISSION,
            MaterialVersion.material_id.in_(
                select(Material.id).where(Material.registration_id == reg.id)
            ),
        )
        .values(status=MaterialVersionStatus.SUBMITTED)
    )

    # Mark supplementary as used and transition status
    reg.supplementary_used = True
    reg.status = RegistrationStatus.SUPPLEMENTED

    await db.commit()
    for v in created_versions:
        await db.refresh(v)
    return created_versions


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _get_uploadable_registration(
    registration_id: uuid.UUID,
    user: User,
    db: AsyncSession,
    *,
    is_supplementary: bool = False,
) -> Registration:
    """Fetch a registration that the user can upload materials to.

    Enforces deadline rules:
    - Before submission_deadline: normal uploads allowed on drafts/submitted.
    - Between submission_deadline and supplementary_deadline: only via the
      one-time supplementary endpoint (is_supplementary=True).
    - After supplementary_deadline: all uploads blocked.
    """
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    # Only owner or admin
    if user.role != UserRole.SYSTEM_ADMIN and reg.applicant_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")

    # Check upload is allowed based on status.
    # Supplementary uploads are a one-time correction flow for registrations
    # that have already been submitted; drafts must not be advanced to
    # "supplemented" via this path — the audit flagged that gap.
    if is_supplementary:
        if reg.status != RegistrationStatus.SUBMITTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Supplementary submission is only available for registrations "
                    f"in 'submitted' status (current: '{reg.status.value}'). "
                    "Draft registrations must be submitted first via the normal "
                    "submit endpoint."
                ),
            )
    else:
        if reg.status not in (
            RegistrationStatus.DRAFT,
            RegistrationStatus.SUBMITTED,
            RegistrationStatus.SUPPLEMENTED,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload materials when registration status is '{reg.status.value}'",
            )

    # ── Deadline enforcement ───────────────────────────────────────────────
    batch_result = await db.execute(
        select(CollectionBatch).where(CollectionBatch.id == reg.batch_id)
    )
    batch = batch_result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    now = datetime.now(timezone.utc)

    # SQLite returns naive datetimes; normalise to UTC for comparison.
    sub_deadline = batch.submission_deadline
    if sub_deadline is not None and sub_deadline.tzinfo is None:
        sub_deadline = sub_deadline.replace(tzinfo=timezone.utc)

    sup_deadline = batch.supplementary_deadline
    if sup_deadline is not None and sup_deadline.tzinfo is None:
        sup_deadline = sup_deadline.replace(tzinfo=timezone.utc)

    if sub_deadline is not None and now <= sub_deadline:
        # Normal window — regular uploads allowed, but not via supplementary endpoint
        if is_supplementary:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Supplementary submission is only available after the submission deadline",
            )
        return reg

    if sup_deadline is not None and now <= sup_deadline:
        # Supplementary window
        if not is_supplementary:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Submission deadline has passed. Use the supplementary submission endpoint.",
            )
        if reg.supplementary_used:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Supplementary submission has already been used",
            )
        return reg

    # Past both deadlines
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Submission window closed",
    )


async def _get_visible_registration(
    registration_id: uuid.UUID, user: User, db: AsyncSession
) -> Registration:
    """Fetch a registration visible to the user for material read/download.

    Financial administrators are excluded from material access — they do not
    have a prompt-backed need to view or download applicant materials.
    """
    if user.role == UserRole.FINANCIAL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial administrators do not have access to applicant materials.",
        )

    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    if user.role == UserRole.APPLICANT and reg.applicant_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    if user.role == UserRole.REVIEWER and reg.status == RegistrationStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view draft registrations"
        )
    return reg
