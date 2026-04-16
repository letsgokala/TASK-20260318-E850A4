"""Fix supplementary_deadline GENERATED expression to be IMMUTABLE.

PG marks ``timestamptz + interval`` as STABLE (not IMMUTABLE) because the
result of interval arithmetic on timestamptz can depend on session timezone
for interval components larger than hours. GENERATED columns require an
IMMUTABLE expression, so the original ``submission_deadline + interval '72
hours'`` expression was rejected on fresh PG 16 clusters with:

    InvalidObjectDefinitionError: generation expression is not immutable

The SQLite-based test lane hid this because SQLite accepted the expression
as a plain column default. Switching the main test suite to real PG surfaced
the bug.

This migration replaces the expression with an equivalent one that routes
through ``timestamp without time zone`` arithmetic (IMMUTABLE) and converts
back to ``timestamptz``. The computed value is identical for the pure-hours
interval in use.

PostgreSQL does not allow ALTERing a GENERATED column's expression in place,
so we drop and re-add the column. The value is derived, so no data is lost.

Revision ID: 007
Revises: 006
Create Date: 2026-04-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_EXPR = (
    "(submission_deadline AT TIME ZONE 'UTC' + interval '72 hours') "
    "AT TIME ZONE 'UTC'"
)
_OLD_EXPR = "submission_deadline + interval '72 hours'"


def upgrade() -> None:
    # Drop/re-add because PG does not support ALTER COLUMN on a generated
    # expression. The column is derived, so there is no data loss.
    op.drop_column("collection_batches", "supplementary_deadline")
    op.add_column(
        "collection_batches",
        sa.Column(
            "supplementary_deadline",
            sa.DateTime(timezone=True),
            sa.Computed(_NEW_EXPR),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("collection_batches", "supplementary_deadline")
    op.add_column(
        "collection_batches",
        sa.Column(
            "supplementary_deadline",
            sa.DateTime(timezone=True),
            sa.Computed(_OLD_EXPR),
            nullable=False,
        ),
    )
