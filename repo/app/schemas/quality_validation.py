import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.quality_validation import ValidationRuleType, ValidationStatus


class QualityValidationResultResponse(BaseModel):
    id: uuid.UUID
    registration_id: uuid.UUID
    rule_type: ValidationRuleType
    rule_name: str
    status: ValidationStatus
    message: Optional[str] = None
    auto_generated: bool
    checked_by: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ValidationSummary(BaseModel):
    registration_id: uuid.UUID
    total: int
    passed: int
    failed: int
    warnings: int
    results: list[QualityValidationResultResponse]
