import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BatchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    submission_deadline: datetime


class BatchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    submission_deadline: datetime | None = None


class BatchResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    submission_deadline: datetime
    supplementary_deadline: datetime | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
