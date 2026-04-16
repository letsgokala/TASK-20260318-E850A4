import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.notification import ComparisonOp, Severity


class MetricResult(BaseModel):
    metric_name: str
    value: Decimal
    threshold: Decimal | None = None
    comparison: ComparisonOp | None = None
    breached: bool = False


class MetricsResponse(BaseModel):
    batch_id: uuid.UUID | None
    approval_rate: MetricResult
    correction_rate: MetricResult
    overspending_rate: MetricResult


class AlertThresholdResponse(BaseModel):
    id: int
    metric_name: str
    threshold_value: Decimal
    comparison: ComparisonOp
    updated_by: uuid.UUID | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertThresholdUpdate(BaseModel):
    threshold_value: Decimal = Field(..., ge=0, le=100)
    comparison: ComparisonOp


class NotificationResponse(BaseModel):
    id: int
    user_id: uuid.UUID | None
    message: str
    severity: Severity
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
