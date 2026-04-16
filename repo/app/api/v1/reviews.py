import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.registration import Registration, RegistrationStatus
from app.models.review_record import ReviewRecord
from app.models.user import User, UserRole
from app.schemas.review import (
    BatchReviewRequest,
    BatchReviewResponse,
    BatchReviewResultItem,
    ReviewRecordResponse,
    TransitionRequest,
)
from app.workflows.review_states import get_allowed_targets, is_valid_transition

router = APIRouter()


# ── Single transition ──────────────────────────────────────────────────────

@router.post(
    "/registrations/{registration_id}/transition",
    response_model=ReviewRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def transition_registration(
    registration_id: uuid.UUID,
    body: TransitionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Registration).where(Registration.id == registration_id)
    )
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    # Applicant can only cancel their own
    if current_user.role == UserRole.APPLICANT:
        if reg.applicant_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
        if body.to_status != RegistrationStatus.CANCELED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Applicants can only cancel their own registrations",
            )

    if current_user.role == UserRole.FINANCIAL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial admins cannot perform review transitions",
        )

    if not is_valid_transition(reg.status, body.to_status, current_user.role):
        allowed = get_allowed_targets(reg.status, current_user.role)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Cannot transition from '{reg.status.value}' to '{body.to_status.value}'",
                "allowed_transitions": [s.value for s in allowed],
            },
        )

    record = ReviewRecord(
        registration_id=reg.id,
        from_status=reg.status.value,
        to_status=body.to_status.value,
        comment=body.comment,
        reviewed_by=current_user.id,
    )
    reg.status = body.to_status

    db.add(record)

    # Evaluate alert thresholds BEFORE committing so the transition and any
    # alert notifications land atomically. If alert emission fails under
    # ALERT_FAIL_CLOSED=1, the raise here aborts the commit and the state
    # change does not persist — closing the compliance gap flagged by the
    # audit report (previously the commit happened before this helper ran).
    from app.api.v1.metrics import check_and_notify_breaches
    await check_and_notify_breaches(db)

    await db.commit()
    await db.refresh(record)

    return record


# ── Batch review ───────────────────────────────────────────────────────────

@router.post("/batch", response_model=BatchReviewResponse)
async def batch_review(
    body: BatchReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in (UserRole.REVIEWER, UserRole.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only reviewers and admins can perform batch reviews",
        )

    # Map the narrower BatchReviewAction enum to the full RegistrationStatus
    # so the transition helpers work unchanged.
    target_status = RegistrationStatus(body.action.value)

    results: list[BatchReviewResultItem] = []

    for rid in body.registration_ids:
        # Each entry gets its own savepoint
        try:
            async with db.begin_nested():
                reg_result = await db.execute(
                    select(Registration).where(Registration.id == rid)
                )
                reg = reg_result.scalar_one_or_none()

                if not reg:
                    results.append(BatchReviewResultItem(
                        registration_id=rid, success=False, error="Registration not found"
                    ))
                    continue

                if not is_valid_transition(reg.status, target_status, current_user.role):
                    allowed = get_allowed_targets(reg.status, current_user.role)
                    results.append(BatchReviewResultItem(
                        registration_id=rid,
                        success=False,
                        error=f"Cannot transition from '{reg.status.value}' to '{target_status.value}'. "
                              f"Allowed: {[s.value for s in allowed]}",
                    ))
                    continue

                record = ReviewRecord(
                    registration_id=reg.id,
                    from_status=reg.status.value,
                    to_status=target_status.value,
                    comment=body.comment,
                    reviewed_by=current_user.id,
                )
                reg.status = target_status
                db.add(record)

                results.append(BatchReviewResultItem(
                    registration_id=rid, success=True
                ))
        except Exception as exc:
            results.append(BatchReviewResultItem(
                registration_id=rid, success=False, error=str(exc)
            ))

    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)

    await db.commit()

    if succeeded == 0 and failed > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=BatchReviewResponse(
                results=results, succeeded=succeeded, failed=failed
            ).model_dump(),
        )

    return BatchReviewResponse(results=results, succeeded=succeeded, failed=failed)


# ── Review history for a registration ──────────────────────────────────────

@router.get(
    "/registrations/{registration_id}/history",
    response_model=list[ReviewRecordResponse],
)
async def get_review_history(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify the registration exists and is visible
    reg_result = await db.execute(
        select(Registration).where(Registration.id == registration_id)
    )
    reg = reg_result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    if current_user.role == UserRole.APPLICANT and reg.applicant_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    if current_user.role == UserRole.FINANCIAL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial admins cannot view review history",
        )

    result = await db.execute(
        select(ReviewRecord)
        .where(ReviewRecord.registration_id == registration_id)
        .order_by(ReviewRecord.reviewed_at)
    )
    return result.scalars().all()


# ── Allowed transitions for UI ─────────────────────────────────────────────

@router.get(
    "/registrations/{registration_id}/allowed-transitions",
    response_model=list[str],
)
async def get_allowed_transitions(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Registration).where(Registration.id == registration_id)
    )
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    # Enforce the same visibility rules used by detail/history endpoints
    if current_user.role == UserRole.APPLICANT and reg.applicant_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    if current_user.role == UserRole.FINANCIAL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial admins cannot view review transitions",
        )
    if current_user.role == UserRole.REVIEWER and reg.status == RegistrationStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot view draft registrations",
        )

    targets = get_allowed_targets(reg.status, current_user.role)
    return [t.value for t in targets]
