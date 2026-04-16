"""Admin operations: backups, restore, integrity check, audit log viewer."""
import hashlib
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.read_audit import audit_read
from app.config import settings
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.material import Material, MaterialVersion
from app.models.user import User, UserRole

router = APIRouter()

_admin_only = require_roles(UserRole.SYSTEM_ADMIN)

_BACKUP_DB_DIR = "/backups/db"
_BACKUP_FILES_DIR = "/backups/files"

# Only allow YYYYMMDD date format for backup identifiers
_DATE_PATTERN = re.compile(r"^\d{8}$")


def _parse_db_connection() -> dict:
    """Extract host, port, user, dbname from configured DATABASE_URL."""
    raw = settings.DATABASE_URL
    # Strip async driver prefix before parsing
    normalized = raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    parsed = urlparse(normalized)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "app_user",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/").split("?")[0] or "eagle_point",
    }


# ── Backup list ────────────────────────────────────────────────────────────

@router.get("/backups", response_model=list[dict])
async def list_backups(
    current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
    _audit: None = Depends(audit_read("backup", "list")),
):
    """List available backup dates."""
    backups = []
    if os.path.isdir(_BACKUP_DB_DIR):
        for f in sorted(os.listdir(_BACKUP_DB_DIR), reverse=True):
            if f.startswith("backup_") and f.endswith(".dump"):
                date_str = f.replace("backup_", "").replace(".dump", "")
                file_path = os.path.join(_BACKUP_DB_DIR, f)
                size_bytes = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
                has_files = os.path.isdir(os.path.join(_BACKUP_FILES_DIR, date_str))
                backups.append({
                    "date": date_str,
                    "db_dump": f,
                    "db_size_bytes": size_bytes,
                    "has_file_backup": has_files,
                })
    return backups


# ── Restore ────────────────────────────────────────────────────────────────

@router.post("/backups/{date}/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_backup(
    date: str,
    current_user: User = Depends(_admin_only),
):
    """Trigger a restore from a specific backup date.

    Sets maintenance mode, runs pg_restore + rsync, then clears maintenance.
    """
    # Validate date format to prevent path traversal / injection
    if not _DATE_PATTERN.match(date):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYYMMDD.",
        )

    db_dump = os.path.join(_BACKUP_DB_DIR, f"backup_{date}.dump")
    file_backup_dir = os.path.join(_BACKUP_FILES_DIR, date)

    if not os.path.isfile(db_dump):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No database backup found for date {date}",
        )

    # Enter maintenance mode
    maintenance_file = "/tmp/app_maintenance"
    with open(maintenance_file, "w") as f:
        f.write(f"Restore in progress since {datetime.now(timezone.utc).isoformat()}")

    try:
        # Parse connection details from configured DATABASE_URL — never hardcode the db name
        conn = _parse_db_connection()
        # Run pg_restore using subprocess with explicit arg list (no shell)
        db_result = subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                f"--host={conn['host']}",
                f"--port={conn['port']}",
                f"--username={conn['user']}",
                f"--dbname={conn['dbname']}",
                db_dump,
            ],
            capture_output=True,
            timeout=600,
            env={**os.environ, "PGPASSWORD": conn["password"]},
        )

        # Restore files if backup exists. ``files_attempted`` distinguishes
        # "no file backup present for this date" (not an error, files_exit_code
        # stays 0) from "rsync actually ran and returned 0" (success) vs.
        # "rsync ran and failed" (partial failure — must NOT be reported as
        # ``complete``). The audit previously flagged the old behavior as
        # unsafe because a nonzero rsync exit code was returned as
        # ``status=complete``.
        #
        # Layout awareness: newer backups (post-audit-fix) organise dated
        # dirs as ``<date>/materials/`` and ``<date>/invoices/``. Older
        # backups are a flat ``<date>/`` of material files. The restore
        # handles both by detecting the ``materials/`` subdir.
        files_result_code = 0
        files_stderr = ""
        files_attempted = False
        invoices_result_code = 0
        invoices_stderr = ""
        invoices_attempted = False

        if os.path.isdir(file_backup_dir):
            files_attempted = True
            materials_src = os.path.join(file_backup_dir, "materials")
            invoices_src = os.path.join(file_backup_dir, "invoices")
            if os.path.isdir(materials_src):
                # New layout: restore materials from the subdir and then
                # pull invoices back too if they were captured.
                files_result = subprocess.run(
                    ["rsync", "-a", "--delete", f"{materials_src}/", "/storage/materials/"],
                    capture_output=True,
                    timeout=600,
                )
                files_result_code = files_result.returncode
                files_stderr = files_result.stderr.decode(errors="replace")[:500]

                if os.path.isdir(invoices_src):
                    invoices_attempted = True
                    invoices_result = subprocess.run(
                        ["rsync", "-a", "--delete", f"{invoices_src}/", "/storage/invoices/"],
                        capture_output=True,
                        timeout=600,
                    )
                    invoices_result_code = invoices_result.returncode
                    invoices_stderr = (
                        invoices_result.stderr.decode(errors="replace")[:500]
                    )
            else:
                # Legacy flat layout — treat the whole date dir as
                # materials. Invoices will simply not be restored because
                # they were never captured in that backup.
                files_result = subprocess.run(
                    ["rsync", "-a", "--delete", f"{file_backup_dir}/", "/storage/materials/"],
                    capture_output=True,
                    timeout=600,
                )
                files_result_code = files_result.returncode
                files_stderr = files_result.stderr.decode(errors="replace")[:500]

        db_ok = db_result.returncode == 0
        files_ok = (not files_attempted) or files_result_code == 0
        invoices_ok = (not invoices_attempted) or invoices_result_code == 0

        if db_ok and files_ok and invoices_ok:
            return {
                "status": "complete",
                "detail": f"Restored from backup {date}",
                "db_exit_code": db_result.returncode,
                "files_exit_code": files_result_code,
                "files_attempted": files_attempted,
                "invoices_exit_code": invoices_result_code,
                "invoices_attempted": invoices_attempted,
            }

        # Partial / failed restore — surface each subsystem result so the
        # operator can tell which half succeeded. Only DB success with
        # no file/invoice backup present is a full success (handled
        # above); any rsync failure flips the response to ``partial`` /
        # ``failed`` with failing exit codes included.
        reason_parts = []
        if not db_ok:
            reason_parts.append(
                f"database restore failed with exit code {db_result.returncode}"
            )
        if files_attempted and not files_ok:
            reason_parts.append(
                f"material file restore (rsync) failed with exit code {files_result_code}"
            )
        if invoices_attempted and not invoices_ok:
            reason_parts.append(
                f"invoice file restore (rsync) failed with exit code {invoices_result_code}"
            )
        any_ok = db_ok or files_ok or invoices_ok
        return {
            "status": "partial" if any_ok else "failed",
            "detail": "; ".join(reason_parts) or "Restore did not complete cleanly",
            "db_exit_code": db_result.returncode,
            "db_stderr": db_result.stderr.decode(errors="replace")[:500],
            "files_exit_code": files_result_code,
            "files_stderr": files_stderr,
            "files_attempted": files_attempted,
            "invoices_exit_code": invoices_result_code,
            "invoices_stderr": invoices_stderr,
            "invoices_attempted": invoices_attempted,
        }
    finally:
        # Exit maintenance mode
        if os.path.exists(maintenance_file):
            os.remove(maintenance_file)


# ── Integrity check ───────────────────────────────────────────────────────

@router.post("/integrity-check")
async def integrity_check(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    """Verify all material files exist on disk and SHA-256 hashes match."""
    result = await db.execute(
        select(MaterialVersion).order_by(MaterialVersion.uploaded_at)
    )
    versions = result.scalars().all()

    total = len(versions)
    ok = 0
    missing = []
    hash_mismatch = []

    for v in versions:
        if not os.path.isfile(v.storage_path):
            missing.append({
                "version_id": str(v.id),
                "material_id": str(v.material_id),
                "storage_path": v.storage_path,
            })
            continue

        sha256 = hashlib.sha256()
        with open(v.storage_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)

        actual_hash = sha256.hexdigest()
        if actual_hash != v.sha256_hash:
            hash_mismatch.append({
                "version_id": str(v.id),
                "material_id": str(v.material_id),
                "expected_hash": v.sha256_hash,
                "actual_hash": actual_hash,
                "storage_path": v.storage_path,
            })
            continue

        ok += 1

    return {
        "total": total,
        "ok": ok,
        "missing": missing,
        "hash_mismatch": hash_mismatch,
        "missing_count": len(missing),
        "mismatch_count": len(hash_mismatch),
    }


# ── Audit log viewer ──────────────────────────────────────────────────────

@router.get("/audit-logs")
async def list_audit_logs(
    user_id: Optional[uuid.UUID] = Query(None),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
    _audit: None = Depends(audit_read("audit_log", "query")),
):
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    filters = []
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if from_date:
        filters.append(AuditLog.created_at >= from_date)
    if to_date:
        filters.append(AuditLog.created_at <= to_date)

    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": log.id,
                "user_id": str(log.user_id) if log.user_id else None,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": str(log.resource_id) if log.resource_id else None,
                "details": log.details,
                "ip_address": str(log.ip_address) if log.ip_address else None,
                "user_agent": log.user_agent,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
