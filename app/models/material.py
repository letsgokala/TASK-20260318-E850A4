import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, SmallInteger,
    String, Text, UniqueConstraint, func, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MaterialVersionStatus(str, enum.Enum):
    PENDING_SUBMISSION = "pending_submission"
    SUBMITTED = "submitted"
    NEEDS_CORRECTION = "needs_correction"


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id"), nullable=False, index=True
    )
    checklist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("checklist_items.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MaterialVersion(Base):
    __tablename__ = "material_versions"
    __table_args__ = (
        UniqueConstraint("material_id", "version_number", name="uq_material_version"),
        CheckConstraint("version_number >= 1 AND version_number <= 3", name="ck_version_range"),
        CheckConstraint("file_size_bytes > 0", name="ck_file_size_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("materials.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[MaterialVersionStatus] = mapped_column(
        Enum(MaterialVersionStatus, name="material_version_status", create_constraint=True),
        nullable=False,
        default=MaterialVersionStatus.PENDING_SUBMISSION,
    )
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_versions.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
