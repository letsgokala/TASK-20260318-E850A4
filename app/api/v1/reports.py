import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import async_session, get_db
from app.models.audit_log import AuditLog
from app.utils.emergency_log import record_critical_failure

logger = logging.getLogger(__name__)
from app.models.export_task import ExportStatus, ExportTask
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.reports.generator import (
    generate_audit_report,
    generate_compliance_report,
    generate_reconciliation_report,
    generate_whitelist_report,
)
from app.schemas.report import ExportTaskResponse

router = APIRouter()

_report_roles = require_roles(UserRole.FINANCIAL_ADMIN, UserRole.SYSTEM_ADMIN)

_VALID_REPORT_TYPES = {"reconciliation", "audit", "compliance", "whitelist"}
# These report types expose sensitive audit/access data and are restricted to system_admin.
_ADMIN_ONLY_REPORT_TYPES = {"audit", "compliance", "whitelist"}
_SYNC_ROW_LIMIT = 5000


# ── Generate report ────────────────────────────────────────────────────────

@router.post("/generate/{report_type}", response_model=ExportTaskResponse, status_code=status.HTTP_201_CREATED)
async def generate_report(
    report_type: str,
    background_tasks: BackgroundTasks,
    batch_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_report_roles),
):
    if report_type not in _VALID_REPORT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid report type. Allowed: {', '.join(_VALID_REPORT_TYPES)}",
        )

    # Audit, compliance, and whitelist exports contain sensitive access data.
    # Financial administrators may only export reconciliation reports.
    if report_type in _ADMIN_ONLY_REPORT_TYPES and current_user.role != UserRole.SYSTEM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only system administrators can export audit, compliance, and whitelist reports.",
        )

    # Estimate row count to decide sync vs async
    count_query = (
        select(func.count())
        .select_from(Registration)
        .where(Registration.status != RegistrationStatus.DRAFT)
    )
    if batch_id:
        count_query = count_query.where(Registration.batch_id == batch_id)
    count_result = await db.execute(count_query)
    row_count = count_result.scalar_one()

    # Create export task record
    task = ExportTask(
        report_type=report_type,
        status=ExportStatus.PENDING,
        created_by=current_user.id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    if row_count <= _SYNC_ROW_LIMIT:
        # Synchronous generation
        try:
            file_path = await _run_report(report_type, batch_id, from_date, to_date, db)
            task.status = ExportStatus.COMPLETE
            task.file_path = file_path
            task.completed_at = datetime.now(timezone.utc)
        except Exception as e:
            task.status = ExportStatus.FAILED
            task.error_message = str(e)[:500]
        await db.commit()
        await db.refresh(task)
    else:
        # Async generation via background task
        task.status = ExportStatus.PROCESSING
        await db.commit()
        await db.refresh(task)
        background_tasks.add_task(
            _run_report_background, str(task.id), report_type, batch_id, from_date, to_date
        )

    return task


# ── Poll task status ───────────────────────────────────────────────────────

@router.get("/tasks/{task_id}", response_model=ExportTaskResponse)
async def get_export_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_report_roles),
):
    task = await _get_owned_export_task(task_id, current_user, db)
    return task


# ── List report tasks for the current user ─────────────────────────────────

@router.get("/tasks", response_model=list[ExportTaskResponse])
async def list_export_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_report_roles),
):
    """Return all export tasks visible to the requesting user (own tasks; admin sees all)."""
    query = select(ExportTask)
    if current_user.role != UserRole.SYSTEM_ADMIN:
        query = query.where(ExportTask.created_by == current_user.id)
    query = query.order_by(ExportTask.created_at.desc()).limit(50)
    result = await db.execute(query)
    return result.scalars().all()


# ── Download completed report ──────────────────────────────────────────────

@router.get("/tasks/{task_id}/download")
async def download_report(
    task_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_report_roles),
):
    task = await _get_owned_export_task(task_id, current_user, db)

    if task.status != ExportStatus.COMPLETE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Report is not ready (status: {task.status.value})",
        )

    if not task.file_path or not os.path.isfile(task.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report file not found on disk",
        )

    # Audit: every report download is a sensitive read and must be logged
    # BEFORE the file is streamed. The audit report previously flagged this
    # endpoint as fail-open (``except Exception: pass``), which allowed
    # unaudited access to sensitive exports. The policy is now:
    #   - AUDIT_FAIL_CLOSED=1 (default): audit write failure blocks the
    #     download with a 500.
    #   - AUDIT_FAIL_CLOSED=0: the failure is mirrored to the emergency log
    #     and the download proceeds with an X-Audit-Log-Fallback header.
    audit_entry = AuditLog(
        user_id=current_user.id,
        action=f"DOWNLOAD report/{task.report_type}",
        resource_type="export_task",
        resource_id=task.id,
        details={"report_type": task.report_type, "task_id": str(task.id)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(audit_entry)
    fallback_header: dict[str, str] = {}
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(
            "Report download audit write failed",
            extra={"task_id": str(task.id), "user_id": str(current_user.id)},
            exc_info=True,
        )
        record_critical_failure(
            category="audit_download_report",
            message="failed to persist download audit row",
            task_id=str(task.id),
            report_type=task.report_type,
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
        task.file_path,
        filename=f"{task.report_type}_{task.id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=fallback_header or None,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _run_report(
    report_type: str,
    batch_id: uuid.UUID | None,
    from_date: datetime | None,
    to_date: datetime | None,
    db: AsyncSession,
) -> str:
    if report_type == "reconciliation":
        return await generate_reconciliation_report(db, batch_id, from_date, to_date)
    elif report_type == "audit":
        return await generate_audit_report(db, from_date, to_date)
    elif report_type == "compliance":
        return await generate_compliance_report(db, batch_id, from_date, to_date)
    elif report_type == "whitelist":
        return await generate_whitelist_report(db, batch_id)
    raise ValueError(f"Unknown report type: {report_type}")


async def _run_report_background(
    task_id: str,
    report_type: str,
    batch_id: uuid.UUID | None,
    from_date: datetime | None,
    to_date: datetime | None,
) -> None:
    """Background task for large reports — uses its own DB session."""
    async with async_session() as db:
        result = await db.execute(
            select(ExportTask).where(ExportTask.id == uuid.UUID(task_id))
        )
        task = result.scalar_one_or_none()
        if not task:
            return

        try:
            file_path = await _run_report(report_type, batch_id, from_date, to_date, db)
            task.status = ExportStatus.COMPLETE
            task.file_path = file_path
            task.completed_at = datetime.now(timezone.utc)
        except Exception as e:
            task.status = ExportStatus.FAILED
            task.error_message = str(e)[:500]

        await db.commit()


async def _get_owned_export_task(
    task_id: uuid.UUID, current_user: User, db: AsyncSession
) -> ExportTask:
    """Fetch an export task, scoped to the creator (system_admin can see all)."""
    result = await db.execute(select(ExportTask).where(ExportTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export task not found")
    if current_user.role != UserRole.SYSTEM_ADMIN and task.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your export task")
    return task
