"""Quality validation results — persisted outcomes of rule-based checks
on registrations and their materials."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, SmallInteger, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ValidationRuleType(str, enum.Enum):
    REQUIRED_FIELD = "required_field"
    DATE_ORDER = "date_order"
    BUDGET_POSITIVE = "budget_positive"
    MATERIAL_COMPLETE = "material_complete"
    CUSTOM = "custom"


class ValidationStatus(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class QualityValidationResult(Base):
    """Persisted result of a quality validation check run against a registration."""

    __tablename__ = "quality_validation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_type: Mapped[ValidationRuleType] = mapped_column(
        Enum(ValidationRuleType, name="validation_rule_type", create_constraint=True),
        nullable=False,
    )
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="validation_status", create_constraint=True),
        nullable=False,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True if this result was generated automatically (e.g. on submission)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
