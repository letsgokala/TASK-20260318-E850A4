"""Duplicate/similarity-check API — disabled by default via config flag."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.material import Material, MaterialVersion
from app.models.registration import Registration, RegistrationStatus
from app.models.user import User, UserRole
from app.schemas.report import DuplicateMatch

router = APIRouter()

# Restrict duplicate lookup to reviewer/admin — prevents cross-registration metadata
# leakage to lower-privileged authenticated users.
_reviewer_or_admin = require_roles(UserRole.REVIEWER, UserRole.SYSTEM_ADMIN)


@router.get("/duplicates", response_model=list[DuplicateMatch])
async def check_duplicates(
    hash: str = Query(..., min_length=64, max_length=64, description="SHA-256 hash"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_reviewer_or_admin),
):
    """Find material versions matching the given SHA-256 hash.

    Results are scoped to non-draft registrations only, preventing
    cross-user draft metadata from leaking through hash lookups.
    """
    result = await db.execute(
        select(
            MaterialVersion.id.label("version_id"),
            MaterialVersion.material_id,
            Material.registration_id,
            MaterialVersion.original_filename,
            MaterialVersion.uploaded_at,
        )
        .join(Material, MaterialVersion.material_id == Material.id)
        .join(Registration, Material.registration_id == Registration.id)
        .where(
            MaterialVersion.sha256_hash == hash,
            Registration.status != RegistrationStatus.DRAFT,
        )
        .order_by(MaterialVersion.uploaded_at)
    )
    rows = result.all()
    return [
        DuplicateMatch(
            version_id=r.version_id,
            material_id=r.material_id,
            registration_id=r.registration_id,
            original_filename=r.original_filename,
            uploaded_at=r.uploaded_at,
        )
        for r in rows
    ]
