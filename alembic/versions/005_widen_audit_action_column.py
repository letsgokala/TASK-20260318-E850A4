"""Widen audit_logs.action from 50 to 255 characters

Revision ID: 005
Revises: 004
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "action",
        existing_type=sa.String(50),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "action",
        existing_type=sa.String(255),
        type_=sa.String(50),
        existing_nullable=False,
    )
