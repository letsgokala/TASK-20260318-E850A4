"""Add review_records, funding_accounts, financial_transactions

Revision ID: 003
Revises: 002
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_transaction_type = sa.Enum("income", "expense", name="transaction_type")


def upgrade() -> None:
    # --- review_records ---
    op.create_table(
        "review_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("registration_id", UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=False, index=True),
        sa.Column("from_status", sa.String(50), nullable=False),
        sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- funding_accounts ---
    op.create_table(
        "funding_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("registration_id", UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("allocated_budget", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- financial_transactions ---
    op.create_table(
        "financial_transactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("funding_account_id", UUID(as_uuid=True), sa.ForeignKey("funding_accounts.id"), nullable=False, index=True),
        sa.Column("type", _transaction_type, nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("invoice_attachment_path", sa.String(500), nullable=True),
        sa.Column("recorded_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_check_constraint("ck_transaction_amount_positive", "financial_transactions", "amount > 0")

    # Auto-update trigger for funding_accounts.updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_funding_accounts_updated_at
        BEFORE UPDATE ON funding_accounts
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_funding_accounts_updated_at ON funding_accounts;")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column();")
    op.drop_table("financial_transactions")
    op.drop_table("funding_accounts")
    op.drop_table("review_records")
    _transaction_type.drop(op.get_bind(), checkfirst=True)
