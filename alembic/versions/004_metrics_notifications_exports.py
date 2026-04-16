"""Add alert_thresholds, notifications, export_tasks; seed default thresholds

Revision ID: 004
Revises: 003
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_comparison_op = sa.Enum("gt", "lt", name="comparison_op")
_notification_severity = sa.Enum("warning", "critical", name="notification_severity")
_export_status = sa.Enum("pending", "processing", "complete", "failed", name="export_status")


def upgrade() -> None:
    # --- alert_thresholds ---
    op.create_table(
        "alert_thresholds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("metric_name", sa.String(50), unique=True, nullable=False),
        sa.Column("threshold_value", sa.Numeric(5, 2), nullable=False),
        sa.Column("comparison", _comparison_op, nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Seed default thresholds
    op.execute("""
        INSERT INTO alert_thresholds (metric_name, threshold_value, comparison)
        VALUES
            ('approval_rate', 50.00, 'lt'),
            ('correction_rate', 30.00, 'gt'),
            ('overspending_rate', 15.00, 'gt')
    """)

    # --- notifications ---
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", _notification_severity, nullable=False, server_default="warning"),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- export_tasks ---
    op.create_table(
        "export_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_type", sa.String(50), nullable=False),
        sa.Column("status", _export_status, nullable=False, server_default="pending"),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("export_tasks")
    op.drop_table("notifications")
    op.drop_table("alert_thresholds")
    _export_status.drop(op.get_bind(), checkfirst=True)
    _notification_severity.drop(op.get_bind(), checkfirst=True)
    _comparison_op.drop(op.get_bind(), checkfirst=True)
