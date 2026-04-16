"""Add collection_batches, checklist_items, registrations, materials, material_versions

Revision ID: 002
Revises: 001
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_registration_status = sa.Enum(
    "draft", "submitted", "supplemented", "approved", "rejected",
    "waitlisted", "promoted_from_waitlist", "canceled",
    name="registration_status",
)

_material_version_status = sa.Enum(
    "pending_submission", "submitted", "needs_correction",
    name="material_version_status",
)


def upgrade() -> None:
    # --- collection_batches ---
    op.create_table(
        "collection_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supplementary_deadline",
            sa.DateTime(timezone=True),
            # ``timestamptz + interval`` is STABLE in PG (session-timezone
            # dependent for interval components larger than hours), which the
            # GENERATED column immutability check rejects. Route through
            # ``timestamp without time zone`` — identical result for pure-hour
            # intervals, but the intermediate operators are IMMUTABLE.
            sa.Computed(
                "(submission_deadline AT TIME ZONE 'UTC' + interval '72 hours') "
                "AT TIME ZONE 'UTC'"
            ),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- checklist_items ---
    op.create_table(
        "checklist_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("collection_batches.id"), nullable=False, index=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- registrations ---
    op.create_table(
        "registrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("collection_batches.id"), nullable=False, index=True),
        sa.Column("applicant_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", _registration_status, nullable=False, server_default="draft"),
        sa.Column("wizard_step", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        # Form fields
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("activity_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_budget", sa.Numeric(14, 2), nullable=True),
        # PII fields (encrypted at rest)
        sa.Column("applicant_name", sa.String(255), nullable=True),
        sa.Column("applicant_id_number", sa.String(500), nullable=True),
        sa.Column("applicant_phone", sa.String(500), nullable=True),
        sa.Column("applicant_email", sa.String(500), nullable=True),
        # Supplementary
        sa.Column("supplementary_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- materials ---
    op.create_table(
        "materials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("registration_id", UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=False, index=True),
        sa.Column("checklist_item_id", UUID(as_uuid=True), sa.ForeignKey("checklist_items.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- material_versions ---
    op.create_table(
        "material_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_id", UUID(as_uuid=True), sa.ForeignKey("materials.id"), nullable=False, index=True),
        sa.Column("version_number", sa.SmallInteger(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False, index=True),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("status", _material_version_status, nullable=False, server_default="pending_submission"),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("duplicate_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("duplicate_of", UUID(as_uuid=True), sa.ForeignKey("material_versions.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    )

    op.create_unique_constraint("uq_material_version", "material_versions", ["material_id", "version_number"])
    op.create_check_constraint("ck_version_range", "material_versions", "version_number >= 1 AND version_number <= 3")
    op.create_check_constraint("ck_file_size_positive", "material_versions", "file_size_bytes > 0")


def downgrade() -> None:
    op.drop_table("material_versions")
    op.drop_table("materials")
    op.drop_table("registrations")
    op.drop_table("checklist_items")
    op.drop_table("collection_batches")
    _material_version_status.drop(op.get_bind(), checkfirst=True)
    _registration_status.drop(op.get_bind(), checkfirst=True)
