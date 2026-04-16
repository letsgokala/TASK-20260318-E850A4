import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.auth.read_audit import audit_read
from app.database import get_db
from app.models.financial import FinancialTransaction, FundingAccount, TransactionType
from app.models.material import Material, MaterialVersion, MaterialVersionStatus
from app.models.notification import AlertThreshold, ComparisonOp, Notification, Severity
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.schemas.metrics import (
    AlertThresholdResponse,
    AlertThresholdUpdate,
    MetricResult,
    MetricsResponse,
    NotificationResponse,
)

router = APIRouter()

_admin_only = require_roles(UserRole.SYSTEM_ADMIN)


def _unresolved_correction_count_query(base_filter: list):
    """Count registrations with at least one material whose **latest**
    version is still ``needs_correction``.

    A registration where version 1 was ``needs_correction`` but
    version 2 is ``submitted`` is *resolved* and must NOT count.
    Both ``get_metrics`` and ``check_and_notify_breaches`` use this so
    the correction-rate and alert thresholds share the same semantics.

    Strategy: for each material slot, find the latest version_number
    via a correlated subquery, then check whether that row's status is
    ``needs_correction``.
    """
    from sqlalchemy import and_

    # Subquery: latest version_number per material
    latest_ver_num = (
        select(func.max(MaterialVersion.version_number))
        .where(MaterialVersion.material_id == Material.id)
        .correlate(Material)
        .scalar_subquery()
    )

    # Registrations that have at least one material where the latest
    # version is needs_correction.
    return (
        select(func.count(func.distinct(Material.registration_id)))
        .select_from(MaterialVersion)
        .join(Material, MaterialVersion.material_id == Material.id)
        .join(Registration, Material.registration_id == Registration.id)
        .where(
            MaterialVersion.status == MaterialVersionStatus.NEEDS_CORRECTION,
            MaterialVersion.version_number == latest_ver_num,
            *base_filter,
        )
    )


# ── Metrics computation ────────────────────────────────────────────────────

@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    batch_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in (
        UserRole.REVIEWER, UserRole.SYSTEM_ADMIN, UserRole.FINANCIAL_ADMIN
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    # Build base filter — exclude drafts
    non_draft = [s for s in RegistrationStatus if s != RegistrationStatus.DRAFT]
    base_filter = [Registration.status.in_(non_draft)]
    if batch_id:
        base_filter.append(Registration.batch_id == batch_id)

    # Total registrations (non-draft)
    total_result = await db.execute(
        select(func.count()).select_from(Registration).where(*base_filter)
    )
    total = total_result.scalar_one()

    # Approved count
    approved_result = await db.execute(
        select(func.count()).select_from(Registration).where(
            *base_filter,
            Registration.status.in_([
                RegistrationStatus.APPROVED,
                RegistrationStatus.PROMOTED_FROM_WAITLIST,
            ]),
        )
    )
    approved = approved_result.scalar_one()

    # Correction count: registrations where at least one material's LATEST
    # version is still needs_correction. Resolved corrections (newer
    # version uploaded) no longer inflate the rate.
    correction_result = await db.execute(
        _unresolved_correction_count_query(base_filter)
    )
    corrections = correction_result.scalar_one()

    # Overspending rate
    total_accounts_result = await db.execute(
        select(func.count()).select_from(FundingAccount)
        .join(Registration, FundingAccount.registration_id == Registration.id)
        .where(*base_filter)
    )
    total_accounts = total_accounts_result.scalar_one()

    # Accounts where expenses > allocated_budget
    over_budget_subq = (
        select(FundingAccount.id)
        .join(Registration, FundingAccount.registration_id == Registration.id)
        .where(*base_filter)
        .group_by(FundingAccount.id, FundingAccount.allocated_budget)
        .having(
            func.coalesce(
                select(func.sum(FinancialTransaction.amount))
                .where(
                    FinancialTransaction.funding_account_id == FundingAccount.id,
                    FinancialTransaction.type == TransactionType.EXPENSE,
                )
                .correlate(FundingAccount)
                .scalar_subquery(),
                Decimal("0"),
            ) > FundingAccount.allocated_budget
        )
    ).subquery()

    over_budget_result = await db.execute(
        select(func.count()).select_from(over_budget_subq)
    )
    over_budget_count = over_budget_result.scalar_one()

    # Compute rates
    approval_rate = Decimal(str(round(approved / total * 100, 2))) if total > 0 else Decimal("0")
    correction_rate = Decimal(str(round(corrections / total * 100, 2))) if total > 0 else Decimal("0")
    overspending_rate = (
        Decimal(str(round(over_budget_count / total_accounts * 100, 2)))
        if total_accounts > 0
        else Decimal("0")
    )

    # Load thresholds
    thresholds = {}
    th_result = await db.execute(select(AlertThreshold))
    for th in th_result.scalars().all():
        thresholds[th.metric_name] = th

    # Build results and check breaches
    metrics = {
        "approval_rate": approval_rate,
        "correction_rate": correction_rate,
        "overspending_rate": overspending_rate,
    }

    results = {}
    for name, value in metrics.items():
        th = thresholds.get(name)
        breached = False
        if th:
            if th.comparison == ComparisonOp.GT and value > th.threshold_value:
                breached = True
            elif th.comparison == ComparisonOp.LT and value < th.threshold_value:
                breached = True

        results[name] = MetricResult(
            metric_name=name,
            value=value,
            threshold=th.threshold_value if th else None,
            comparison=th.comparison if th else None,
            breached=breached,
        )

    # GET /metrics is intentionally side-effect-free.
    # Notifications are created by the write-side event helper below.
    return MetricsResponse(
        batch_id=batch_id,
        approval_rate=results["approval_rate"],
        correction_rate=results["correction_rate"],
        overspending_rate=results["overspending_rate"],
    )


# ── Shared write-side helper: evaluate thresholds and notify ──────────────────

async def check_and_notify_breaches(db: AsyncSession) -> None:
    """Evaluate alert thresholds against current metric values and stage
    deduped breach notifications on the caller's session.

    IMPORTANT — transactional contract:
      This helper **does not commit**. Callers must commit the outer unit of
      work once, so the domain mutation (finance transaction, review
      transition) and any alert notification either both persist or both
      roll back. The audit report flagged the prior post-commit call-site
      as a compliance gap: a threshold/notification failure could leave a
      committed finance transaction or review state change behind. With the
      helper now pre-commit, ``ALERT_FAIL_CLOSED=1`` raises before the
      handler's commit — the whole unit of work rolls back atomically.

    Autoflush on the caller's session makes the queries below see the
    yet-uncommitted state of the current request, so thresholds are
    evaluated against the state the caller is about to commit.
    """
    try:
        non_draft = [s for s in RegistrationStatus if s != RegistrationStatus.DRAFT]

        total_result = await db.execute(
            select(func.count()).select_from(Registration).where(
                Registration.status.in_(non_draft)
            )
        )
        total = total_result.scalar_one()
        if total == 0:
            return

        approved_result = await db.execute(
            select(func.count()).select_from(Registration).where(
                Registration.status.in_([
                    RegistrationStatus.APPROVED,
                    RegistrationStatus.PROMOTED_FROM_WAITLIST,
                ])
            )
        )
        approved = approved_result.scalar_one()

        corrections = (await db.execute(
            _unresolved_correction_count_query([Registration.status.in_(non_draft)])
        )).scalar_one()

        total_accounts_result = await db.execute(
            select(func.count()).select_from(FundingAccount)
            .join(Registration, FundingAccount.registration_id == Registration.id)
            .where(Registration.status.in_(non_draft))
        )
        total_accounts = total_accounts_result.scalar_one()

        over_budget_subq = (
            select(FundingAccount.id)
            .join(Registration, FundingAccount.registration_id == Registration.id)
            .where(Registration.status.in_(non_draft))
            .group_by(FundingAccount.id, FundingAccount.allocated_budget)
            .having(
                func.coalesce(
                    select(func.sum(FinancialTransaction.amount))
                    .where(
                        FinancialTransaction.funding_account_id == FundingAccount.id,
                        FinancialTransaction.type == TransactionType.EXPENSE,
                    )
                    .correlate(FundingAccount)
                    .scalar_subquery(),
                    Decimal("0"),
                ) > FundingAccount.allocated_budget
            )
        ).subquery()
        over_budget_count = (
            await db.execute(select(func.count()).select_from(over_budget_subq))
        ).scalar_one()

        approval_rate = Decimal(str(round(approved / total * 100, 2)))
        correction_rate = Decimal(str(round(corrections / total * 100, 2)))
        overspending_rate = (
            Decimal(str(round(over_budget_count / total_accounts * 100, 2)))
            if total_accounts > 0 else Decimal("0")
        )

        current_metrics = {
            "approval_rate": approval_rate,
            "correction_rate": correction_rate,
            "overspending_rate": overspending_rate,
        }

        th_result = await db.execute(select(AlertThreshold))
        thresholds = {th.metric_name: th for th in th_result.scalars().all()}

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for name, value in current_metrics.items():
            th = thresholds.get(name)
            if not th:
                continue
            breached = (
                (th.comparison == ComparisonOp.GT and value > th.threshold_value) or
                (th.comparison == ComparisonOp.LT and value < th.threshold_value)
            )
            if not breached:
                continue
            # Deduplicate: one notification per metric per day. The dedup
            # query autoflushes pending notifications, so a second in-flight
            # call within the same transaction will not double-stage.
            dup = (await db.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.message.contains(f"Alert: {name}"),
                    Notification.created_at >= today_start,
                )
            )).scalar_one()
            if dup == 0:
                db.add(Notification(
                    user_id=None,  # visible to management roles only
                    message=(
                        f"Alert: {name} is {value}% "
                        f"(threshold: {th.comparison.value} {th.threshold_value}%)"
                    ),
                    severity=(
                        Severity.CRITICAL if name == "overspending_rate"
                        else Severity.WARNING
                    ),
                ))

        # Intentionally no commit — the caller owns the unit of work.

    except Exception as exc:
        # Alert generation is critical — a silent failure means operators
        # will not see threshold breaches. By default (ALERT_FAIL_CLOSED=1)
        # we propagate so the metrics read itself returns 5xx rather than
        # silently succeeding with a missing alert. Operators who prefer
        # metrics-read availability over strict alert coverage can set
        # ALERT_FAIL_CLOSED=0 to fall back to the prior fail-open behavior.
        # Mirror the failure to the filesystem emergency log regardless so
        # the failed attempt is recoverable without centralized logging.
        logger.error(
            "check_and_notify_breaches failed to emit alerts",
            exc_info=True,
        )
        from app.utils.emergency_log import record_critical_failure
        record_critical_failure(
            category="alert_emission",
            message="check_and_notify_breaches failed",
            error=repr(exc),
        )
        # Local import keeps test-time config reloads visible.
        from app.config import settings as _settings
        if _settings.ALERT_FAIL_CLOSED == "1":
            raise RuntimeError(
                "Alert emission failed and ALERT_FAIL_CLOSED=1"
            ) from exc


# ── Alert thresholds ──────────────────────────────────────────────────────

@router.get("/alert-thresholds", response_model=list[AlertThresholdResponse])
async def list_alert_thresholds(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    result = await db.execute(select(AlertThreshold).order_by(AlertThreshold.metric_name))
    return result.scalars().all()


@router.put("/alert-thresholds/{threshold_id}", response_model=AlertThresholdResponse)
async def update_alert_threshold(
    threshold_id: int,
    body: AlertThresholdUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    result = await db.execute(select(AlertThreshold).where(AlertThreshold.id == threshold_id))
    threshold = result.scalar_one_or_none()
    if not threshold:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Threshold not found")

    threshold.threshold_value = body.threshold_value
    threshold.comparison = body.comparison
    threshold.updated_by = current_user.id
    await db.commit()
    await db.refresh(threshold)
    return threshold


# ── Notifications ──────────────────────────────────────────────────────────

_MANAGEMENT_ROLES = {UserRole.REVIEWER, UserRole.FINANCIAL_ADMIN, UserRole.SYSTEM_ADMIN}


@router.get("/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    unread: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _audit: None = Depends(audit_read("notification", "list")),
):
    # Applicants only see their own notifications; management roles also see
    # global alert notifications (user_id IS NULL).
    if current_user.role in _MANAGEMENT_ROLES:
        query = select(Notification).where(
            (Notification.user_id == current_user.id) | (Notification.user_id.is_(None))
        )
    else:
        query = select(Notification).where(Notification.user_id == current_user.id)

    if unread is not None:
        query = query.where(Notification.read == (not unread))

    offset = (page - 1) * page_size
    query = query.order_by(Notification.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    # Global notifications (user_id=None) are management alerts — applicants cannot access them.
    if notif.user_id is None and current_user.role not in _MANAGEMENT_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your notification")

    # User-specific notifications can only be marked by their owner.
    if notif.user_id is not None and notif.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your notification")

    notif.read = True
    await db.commit()
