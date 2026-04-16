import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Numeric, SmallInteger,
    String, Text, func, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ComparisonOp(str, enum.Enum):
    GT = "gt"
    LT = "lt"


class Severity(str, enum.Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertThreshold(Base):
    __tablename__ = "alert_thresholds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    metric_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    comparison: Mapped[ComparisonOp] = mapped_column(
        Enum(ComparisonOp, name="comparison_op", create_constraint=True),
        nullable=False,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="notification_severity", create_constraint=True),
        nullable=False,
        default=Severity.WARNING,
    )
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
