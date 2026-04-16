import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Numeric, SmallInteger,
    String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegistrationStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    SUPPLEMENTED = "supplemented"
    APPROVED = "approved"
    REJECTED = "rejected"
    WAITLISTED = "waitlisted"
    PROMOTED_FROM_WAITLIST = "promoted_from_waitlist"
    CANCELED = "canceled"


class Registration(Base):
    __tablename__ = "registrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collection_batches.id"), nullable=False, index=True
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[RegistrationStatus] = mapped_column(
        Enum(RegistrationStatus, name="registration_status", create_constraint=True),
        nullable=False,
        default=RegistrationStatus.DRAFT,
    )

    # Wizard state
    wizard_step: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)

    # Form fields (all nullable for draft support)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    activity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Applicant PII — encrypted at rest via application layer
    applicant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicant_id_number: Mapped[str | None] = mapped_column(String(500), nullable=True)
    applicant_phone: Mapped[str | None] = mapped_column(String(500), nullable=True)
    applicant_email: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Supplementary submission tracking
    supplementary_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
