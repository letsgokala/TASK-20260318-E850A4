import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChecklistItemCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    is_required: bool = True
    sort_order: int = 0


class ChecklistItemResponse(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    label: str
    description: str | None
    is_required: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}
