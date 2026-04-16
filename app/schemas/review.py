import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.registration import RegistrationStatus


class TransitionRequest(BaseModel):
    to_status: RegistrationStatus
    comment: str | None = None


class ReviewRecordResponse(BaseModel):
    id: uuid.UUID
    registration_id: uuid.UUID
    from_status: str
    to_status: str
    comment: str | None
    reviewed_by: uuid.UUID
    reviewed_at: datetime

    model_config = {"from_attributes": True}


class BatchReviewAction(str, enum.Enum):
    """Allowed batch-review actions per the API spec.

    The audit flagged that the prior schema accepted any
    ``RegistrationStatus``, including ``canceled`` and ``draft``, which
    is outside the documented batch-review workflow
    (``approved | rejected | waitlisted``).
    """
    APPROVED = "approved"
    REJECTED = "rejected"
    WAITLISTED = "waitlisted"


class BatchReviewRequest(BaseModel):
    action: BatchReviewAction = Field(
        ..., description="Target status: approved, rejected, or waitlisted"
    )
    comment: str | None = None
    registration_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)


class BatchReviewResultItem(BaseModel):
    registration_id: uuid.UUID
    success: bool
    error: str | None = None


class BatchReviewResponse(BaseModel):
    results: list[BatchReviewResultItem]
    succeeded: int
    failed: int
