"""Add quality_validation_results table

Revision ID: 006
Revises: 005
Create Date: 2026-04-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quality_validation_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "registration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("registrations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "rule_type",
            sa.Enum(
                "required_field",
                "date_order",
                "budget_positive",
                "material_complete",
                "custom",
                name="validation_rule_type",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("rule_name", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pass",
                "fail",
                "warning",
                name="validation_status",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("auto_generated", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "checked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_quality_validation_results_registration_id",
        "quality_validation_results",
        ["registration_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quality_validation_results_registration_id",
        table_name="quality_validation_results",
    )
    op.drop_table("quality_validation_results")
    op.execute("DROP TYPE IF EXISTS validation_rule_type")
    op.execute("DROP TYPE IF EXISTS validation_status")
