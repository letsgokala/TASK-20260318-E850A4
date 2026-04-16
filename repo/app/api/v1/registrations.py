import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.read_audit import audit_read
from app.database import get_db
from app.models.checklist_item import ChecklistItem
from app.models.collection_batch import CollectionBatch
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.schemas.registration import (
    PaginatedRegistrations,
    RegistrationCreate,
    RegistrationDraftUpdate,
    RegistrationListItem,
    RegistrationResponse,
)
from app.utils.encryption import decrypt_value, encrypt_value

# Imported lazily inside the submit handler to avoid circular imports
def _auto_validate(reg, user, db):
    from app.api.v1.quality_validation import auto_validate_on_submit
    return auto_validate_on_submit(reg, user, db)

router = APIRouter()

_PII_FIELDS = ("applicant_id_number", "applicant_phone", "applicant_email")

# Required fields for full submission
_SUBMIT_REQUIRED = ("title", "activity_type", "description", "applicant_name")


def _encrypt_pii(data: dict) -> dict:
    for field in _PII_FIELDS:
        if field in data and data[field] is not None:
            data[field] = encrypt_value(data[field])
    return data


def _decrypt_pii_on_model(reg: Registration) -> Registration:
    for field in _PII_FIELDS:
        val = getattr(reg, field, None)
        if val is not None:
            object.__setattr__(reg, field, decrypt_value(val))
    return reg


def _normalize_utc(value: datetime | None) -> datetime | None:
    """Treat naive DB timestamps as UTC for cross-deploy safety."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _assert_batch_window(
    batch_id: uuid.UUID,
    db: AsyncSession,
    *,
    action: str,
) -> CollectionBatch:
    """Return the batch and enforce the batch submission window.

    Mirrors the material-upload deadline policy already in
    ``materials._get_uploadable_registration`` so create/edit/submit
    cannot bypass the deadline the Prompt requires. The rules are:

    - Before ``submission_deadline``: normal window, any draft lifecycle
      action is allowed.
    - Between ``submission_deadline`` and ``supplementary_deadline``:
      the main submission window is closed; only the supplementary
      endpoint (``materials.supplementary_submit``) is allowed to touch
      the registration.
    - After ``supplementary_deadline``: the batch is fully locked.

    Raises 404 when the batch is missing so a stale ``batch_id`` cannot
    be used to probe the deadline policy.
    """
    batch_result = await db.execute(
        select(CollectionBatch).where(CollectionBatch.id == batch_id)
    )
    batch = batch_result.scalar_one_or_none()
    if not batch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
        )

    now = datetime.now(timezone.utc)
    submission_deadline = _normalize_utc(batch.submission_deadline)
    supplementary_deadline = _normalize_utc(batch.supplementary_deadline)

    if submission_deadline is not None and now <= submission_deadline:
        return batch  # normal window — allow

    # Past the main submission deadline — create/draft-update/submit are
    # all disallowed. The supplementary window is reserved for the
    # dedicated materials.supplementary_submit endpoint and does not
    # permit new drafts, draft edits, or a first-time submit.
    if (
        supplementary_deadline is not None
        and now <= supplementary_deadline
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cannot {action}: submission deadline has passed. Only "
                "the one-time supplementary submission endpoint is "
                "available until the supplementary deadline."
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Cannot {action}: submission window is closed for this batch.",
    )


# ── Create draft ────────────────────────────────────────────────────────────

@router.post("", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
async def create_registration(
    body: RegistrationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in (UserRole.APPLICANT, UserRole.SYSTEM_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only applicants can create registrations")

    # Enforce the batch submission window — late drafts are rejected.
    # System admins are intentionally NOT exempt: the Prompt's deadline
    # is a business-facing guarantee, not an operational convenience.
    await _assert_batch_window(body.batch_id, db, action="create registration")

    data = _encrypt_pii(body.model_dump())
    reg = Registration(applicant_id=current_user.id, **data)
    db.add(reg)
    await db.commit()
    await db.refresh(reg)
    _decrypt_pii_on_model(reg)
    return reg


# ── Autosave draft ──────────────────────────────────────────────────────────

@router.put("/{registration_id}/draft", response_model=RegistrationResponse)
async def update_draft(
    registration_id: uuid.UUID,
    body: RegistrationDraftUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = await _get_owned_registration(registration_id, current_user, db)

    if reg.status != RegistrationStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft registrations can be edited",
        )

    # Deadline enforcement — late draft edits are rejected even though
    # the registration was created in time. Mirrors the materials
    # upload window.
    await _assert_batch_window(reg.batch_id, db, action="edit draft")

    update_data = _encrypt_pii(body.model_dump(exclude_unset=True))
    for field, value in update_data.items():
        setattr(reg, field, value)

    await db.commit()
    await db.refresh(reg)
    _decrypt_pii_on_model(reg)
    return reg


# ── Submit ──────────────────────────────────────────────────────────────────

@router.post("/{registration_id}/submit", response_model=RegistrationResponse)
async def submit_registration(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reg = await _get_owned_registration(registration_id, current_user, db)

    if reg.status != RegistrationStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft registrations can be submitted",
        )

    # Deadline enforcement — block a submit that would otherwise slip
    # past the batch's submission_deadline. After the deadline, the
    # only path forward is the supplementary endpoint on materials,
    # and that path does not produce a first-time submit here.
    await _assert_batch_window(reg.batch_id, db, action="submit registration")

    # Full validation: check required fields
    errors: list[str] = []
    for field in _SUBMIT_REQUIRED:
        if not getattr(reg, field, None):
            errors.append(f"{field} is required")

    if reg.start_date and reg.end_date and reg.end_date <= reg.start_date:
        errors.append("end_date must be after start_date")

    if reg.requested_budget is not None and reg.requested_budget < 0:
        errors.append("requested_budget must be non-negative")

    # Check that required checklist materials are uploaded
    batch_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.batch_id == reg.batch_id, ChecklistItem.is_required == True)
    )
    required_items = batch_result.scalars().all()

    for item in required_items:
        mat_result = await db.execute(
            select(Material).where(
                Material.registration_id == reg.id,
                Material.checklist_item_id == item.id,
            )
        )
        mat = mat_result.scalar_one_or_none()
        if not mat:
            errors.append(f"Required material '{item.label}' not uploaded")
            continue
        # Check it has at least one version
        ver_result = await db.execute(
            select(func.count()).select_from(MaterialVersion).where(
                MaterialVersion.material_id == mat.id
            )
        )
        if ver_result.scalar_one() == 0:
            errors.append(f"Required material '{item.label}' has no uploaded file")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"validation_errors": errors},
        )

    reg.status = RegistrationStatus.SUBMITTED

    # Transition uploaded material versions from pending_submission to
    # submitted in the SAME transaction as the registration status change.
    # The prompt requires the three-state version lifecycle
    # (pending_submission → submitted → needs_correction), so the submit
    # action must advance every latest pending version; versions already
    # marked needs_correction or a prior submitted are left alone — the
    # reviewer, not the submit action, owns those states.
    await _advance_material_versions_on_submit(reg.id, db)

    # Persist quality validation results automatically on submission
    await _auto_validate(reg, current_user, db)
    await db.commit()
    await db.refresh(reg)
    _decrypt_pii_on_model(reg)
    return reg


async def _advance_material_versions_on_submit(
    registration_id: uuid.UUID, db: AsyncSession
) -> None:
    """Advance every ``pending_submission`` material version on a
    registration to ``submitted``. Uses a bulk UPDATE so the transition is
    a single statement scoped to the caller's transaction — if the outer
    commit rolls back, the status advance rolls back with it.
    """
    from sqlalchemy import update

    await db.execute(
        update(MaterialVersion)
        .where(
            MaterialVersion.status == MaterialVersionStatus.PENDING_SUBMISSION,
            MaterialVersion.material_id.in_(
                select(Material.id).where(Material.registration_id == registration_id)
            ),
        )
        .values(status=MaterialVersionStatus.SUBMITTED)
    )


# ── Get single ──────────────────────────────────────────────────────────────

@router.get("/{registration_id}", response_model=RegistrationResponse)
async def get_registration(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _audit: None = Depends(audit_read("registration", "detail", "registration_id")),
):
    reg = await _get_visible_registration(registration_id, current_user, db)
    _decrypt_pii_on_model(reg)

    resp = RegistrationResponse.model_validate(reg)
    is_owner = reg.applicant_id == current_user.id
    return resp.mask_pii(current_user.role, is_owner)


# ── List with pagination ───────────────────────────────────────────────────

@router.get("", response_model=PaginatedRegistrations)
async def list_registrations(
    batch_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[RegistrationStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _audit: None = Depends(audit_read("registration", "list")),
):
    query = select(Registration)
    count_query = select(func.count()).select_from(Registration)

    # Row-level isolation
    if current_user.role == UserRole.APPLICANT:
        query = query.where(Registration.applicant_id == current_user.id)
        count_query = count_query.where(Registration.applicant_id == current_user.id)
    elif current_user.role == UserRole.REVIEWER:
        # Reviewers see only submitted+ registrations
        non_draft = [s for s in RegistrationStatus if s != RegistrationStatus.DRAFT]
        query = query.where(Registration.status.in_(non_draft))
        count_query = count_query.where(Registration.status.in_(non_draft))
    elif current_user.role == UserRole.FINANCIAL_ADMIN:
        non_draft = [s for s in RegistrationStatus if s != RegistrationStatus.DRAFT]
        query = query.where(Registration.status.in_(non_draft))
        count_query = count_query.where(Registration.status.in_(non_draft))
    # system_admin sees everything

    if batch_id:
        query = query.where(Registration.batch_id == batch_id)
        count_query = count_query.where(Registration.batch_id == batch_id)

    if status_filter:
        query = query.where(Registration.status == status_filter)
        count_query = count_query.where(Registration.status == status_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = query.order_by(Registration.updated_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedRegistrations(
        items=[RegistrationListItem.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _get_owned_registration(
    registration_id: uuid.UUID, user: User, db: AsyncSession
) -> Registration:
    """Fetch a registration that belongs to the requesting user (or admin)."""
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")
    if user.role != UserRole.SYSTEM_ADMIN and reg.applicant_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    return reg


async def _get_visible_registration(
    registration_id: uuid.UUID, user: User, db: AsyncSession
) -> Registration:
    """Fetch a registration visible to the requesting user based on role."""
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    if user.role == UserRole.APPLICANT and reg.applicant_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    if user.role in (UserRole.REVIEWER, UserRole.FINANCIAL_ADMIN):
        if reg.status == RegistrationStatus.DRAFT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view draft registrations")
    return reg
