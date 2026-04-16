"""Quality validation: run and persist rule-based checks on registrations."""
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.checklist_item import ChecklistItem
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.quality_validation import (
    QualityValidationResult,
    ValidationRuleType,
    ValidationStatus,
)
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.schemas.quality_validation import QualityValidationResultResponse, ValidationSummary

router = APIRouter()

# Roles that may trigger validation runs
_VALIDATION_ROLES = {UserRole.REVIEWER, UserRole.SYSTEM_ADMIN, UserRole.APPLICANT}


# ── Run validation and persist results ────────────────────────────────────────

@router.post(
    "/{registration_id}/validate",
    response_model=ValidationSummary,
    status_code=status.HTTP_200_OK,
)
async def run_validation(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run all quality-validation rules against a registration and persist outcomes."""
    reg = await _get_accessible_registration(registration_id, current_user, db)

    results = await _run_all_rules(reg, current_user, db)

    # Persist results
    for r in results:
        db.add(r)
    await db.commit()
    for r in results:
        await db.refresh(r)

    return _build_summary(reg.id, results)


# ── List persisted validation results ─────────────────────────────────────────

@router.get(
    "/{registration_id}/validations",
    response_model=ValidationSummary,
)
async def list_validations(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the most-recent persisted validation results for a registration."""
    reg = await _get_accessible_registration(registration_id, current_user, db)

    result = await db.execute(
        select(QualityValidationResult)
        .where(QualityValidationResult.registration_id == reg.id)
        .order_by(QualityValidationResult.created_at.desc())
        .limit(100)
    )
    rows = result.scalars().all()
    return _build_summary(reg.id, rows)


# ── Internal: run all rules ────────────────────────────────────────────────────

async def _run_all_rules(
    reg: Registration,
    triggered_by: User,
    db: AsyncSession,
) -> list[QualityValidationResult]:
    """Execute every validation rule and return unsaved result objects."""
    results: list[QualityValidationResult] = []
    auto = True
    checker_id = triggered_by.id

    def _result(
        rule_type: ValidationRuleType,
        rule_name: str,
        passed: bool,
        message: Optional[str] = None,
        warning: bool = False,
    ) -> QualityValidationResult:
        if passed:
            vstatus = ValidationStatus.PASS
        elif warning:
            vstatus = ValidationStatus.WARNING
        else:
            vstatus = ValidationStatus.FAIL
        return QualityValidationResult(
            registration_id=reg.id,
            rule_type=rule_type,
            rule_name=rule_name,
            status=vstatus,
            message=message,
            auto_generated=auto,
            checked_by=checker_id,
        )

    # ── Required fields ───────────────────────────────────────────────────────
    required_fields = {
        "title": reg.title,
        "activity_type": reg.activity_type,
        "description": reg.description,
        "start_date": reg.start_date,
        "end_date": reg.end_date,
        "requested_budget": reg.requested_budget,
        "applicant_name": reg.applicant_name,
        "applicant_id_number": reg.applicant_id_number,
        "applicant_phone": reg.applicant_phone,
        "applicant_email": reg.applicant_email,
    }
    for field, value in required_fields.items():
        is_present = value is not None and str(value).strip() != ""
        results.append(_result(
            ValidationRuleType.REQUIRED_FIELD,
            f"required_field:{field}",
            passed=is_present,
            message=None if is_present else f"Required field '{field}' is missing or empty.",
        ))

    # ── Date order ────────────────────────────────────────────────────────────
    if reg.start_date and reg.end_date:
        date_ok = reg.start_date < reg.end_date
        results.append(_result(
            ValidationRuleType.DATE_ORDER,
            "date_order:start_before_end",
            passed=date_ok,
            message=None if date_ok else "start_date must be before end_date.",
        ))
    else:
        results.append(_result(
            ValidationRuleType.DATE_ORDER,
            "date_order:start_before_end",
            passed=False,
            message="start_date and/or end_date are missing; cannot check order.",
        ))

    # ── Budget positive ───────────────────────────────────────────────────────
    budget_ok = reg.requested_budget is not None and reg.requested_budget > 0
    results.append(_result(
        ValidationRuleType.BUDGET_POSITIVE,
        "budget:positive_value",
        passed=budget_ok,
        message=None if budget_ok else "requested_budget must be a positive value.",
    ))

    # ── Material completeness: every checklist item has at least one uploaded version ──
    checklist_result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.batch_id == reg.batch_id)
    )
    checklist_items = checklist_result.scalars().all()

    if checklist_items:
        for item in checklist_items:
            mat_result = await db.execute(
                select(Material)
                .where(
                    Material.registration_id == reg.id,
                    Material.checklist_item_id == item.id,
                )
            )
            material = mat_result.scalar_one_or_none()
            has_upload = False
            if material:
                ver_result = await db.execute(
                    select(MaterialVersion)
                    .where(
                        MaterialVersion.material_id == material.id,
                        MaterialVersion.status.notin_(
                            [MaterialVersionStatus.NEEDS_CORRECTION]
                        ),
                    )
                )
                has_upload = ver_result.scalar_one_or_none() is not None

            if item.is_required:
                # Required items: FAIL when missing.
                results.append(_result(
                    ValidationRuleType.MATERIAL_COMPLETE,
                    f"material_complete:item:{item.id}",
                    passed=has_upload,
                    message=(
                        None if has_upload
                        else f"Required checklist item '{item.label}' has no valid material version uploaded."
                    ),
                ))
            else:
                # Optional items: a missing upload is a WARNING, not a
                # hard failure. The audit flagged the prior behaviour as
                # incorrectly penalising registrations for not uploading
                # documents that were explicitly marked optional.
                results.append(_result(
                    ValidationRuleType.MATERIAL_COMPLETE,
                    f"material_complete:item:{item.id}",
                    passed=True if has_upload else False,
                    warning=not has_upload,
                    message=(
                        None if has_upload
                        else f"Optional checklist item '{item.label}' has no material version uploaded."
                    ),
                ))
    else:
        results.append(_result(
            ValidationRuleType.MATERIAL_COMPLETE,
            "material_complete:no_checklist",
            passed=True,
            warning=False,
            message="No checklist items defined for this batch.",
        ))

    return results


def _build_summary(
    registration_id: uuid.UUID,
    results: list,
) -> ValidationSummary:
    passed = sum(1 for r in results if r.status == ValidationStatus.PASS)
    failed = sum(1 for r in results if r.status == ValidationStatus.FAIL)
    warnings = sum(1 for r in results if r.status == ValidationStatus.WARNING)
    return ValidationSummary(
        registration_id=registration_id,
        total=len(results),
        passed=passed,
        failed=failed,
        warnings=warnings,
        results=[QualityValidationResultResponse.model_validate(r) for r in results],
    )


async def _get_accessible_registration(
    registration_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> Registration:
    result = await db.execute(
        select(Registration).where(Registration.id == registration_id)
    )
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    if user.role == UserRole.APPLICANT and reg.applicant_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
    if user.role == UserRole.FINANCIAL_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Financial administrators cannot access quality validation results.",
        )
    if user.role in (UserRole.REVIEWER,) and reg.status == RegistrationStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access draft registration validations.",
        )
    return reg


# ── Shared helper: auto-run validation on registration submission ──────────────

async def auto_validate_on_submit(
    reg: Registration,
    submitted_by: User,
    db: AsyncSession,
) -> None:
    """Run and persist validation automatically when a registration is submitted.
    Failures are logged but do not block submission (enforceable at business layer)."""
    try:
        results = await _run_all_rules(reg, submitted_by, db)
        for r in results:
            db.add(r)
        # Session is already open; caller commits
    except Exception as exc:
        # Validation persistence is important for audit/review traceability.
        # Escalate to ERROR so the failure is surfaced in monitoring. By
        # default (VALIDATION_FAIL_CLOSED=1) we propagate so submissions
        # without a validation record surface as a 5xx — a missing
        # validation record is itself a hard failure for traceability.
        # Operators can set VALIDATION_FAIL_CLOSED=0 to let submits succeed
        # even when validation persistence fails.
        # Mirror to filesystem emergency log so operators can recover the
        # failed registration IDs even without a central log sink.
        logger.error(
            "auto_validate_on_submit failed to persist validation results",
            extra={"registration_id": str(reg.id)},
            exc_info=True,
        )
        from app.utils.emergency_log import record_critical_failure
        record_critical_failure(
            category="validation_persistence",
            message="auto_validate_on_submit failed",
            registration_id=str(reg.id),
            error=repr(exc),
        )
        from app.config import settings as _settings
        if _settings.VALIDATION_FAIL_CLOSED == "1":
            raise RuntimeError(
                "Validation persistence failed and VALIDATION_FAIL_CLOSED=1"
            ) from exc
