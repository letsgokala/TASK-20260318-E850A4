import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_roles
from app.database import get_db
from app.models.checklist_item import ChecklistItem
from app.models.collection_batch import CollectionBatch
from app.models.user import User, UserRole
from app.schemas.batch import BatchCreate, BatchResponse, BatchUpdate
from app.schemas.checklist import ChecklistItemCreate, ChecklistItemResponse

router = APIRouter()

_admin_only = require_roles(UserRole.SYSTEM_ADMIN)


# ── Batch CRUD ──────────────────────────────────────────────────────────────

@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
async def create_batch(
    body: BatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    batch = CollectionBatch(
        name=body.name,
        description=body.description,
        submission_deadline=body.submission_deadline,
        created_by=current_user.id,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return batch


@router.get("", response_model=list[BatchResponse])
async def list_batches(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(CollectionBatch).order_by(CollectionBatch.submission_deadline.desc())
    )
    return result.scalars().all()


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(CollectionBatch).where(CollectionBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


@router.put("/{batch_id}", response_model=BatchResponse)
async def update_batch(
    batch_id: uuid.UUID,
    body: BatchUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    result = await db.execute(select(CollectionBatch).where(CollectionBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(batch, field, value)

    await db.commit()
    await db.refresh(batch)
    return batch


# ── Checklist items ─────────────────────────────────────────────────────────

@router.post(
    "/{batch_id}/checklist",
    response_model=ChecklistItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_checklist_item(
    batch_id: uuid.UUID,
    body: ChecklistItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_admin_only),
):
    # Verify batch exists
    result = await db.execute(select(CollectionBatch).where(CollectionBatch.id == batch_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    item = ChecklistItem(
        batch_id=batch_id,
        label=body.label,
        description=body.description,
        is_required=body.is_required,
        sort_order=body.sort_order,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/{batch_id}/checklist", response_model=list[ChecklistItemResponse])
async def list_checklist_items(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.batch_id == batch_id)
        .order_by(ChecklistItem.sort_order)
    )
    return result.scalars().all()
