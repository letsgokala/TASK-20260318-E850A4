import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.financial import TransactionType


class FundingAccountCreate(BaseModel):
    registration_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    allocated_budget: Decimal = Field(..., ge=0, decimal_places=2)


class FundingAccountResponse(BaseModel):
    id: uuid.UUID
    registration_id: uuid.UUID
    name: str
    allocated_budget: Decimal
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FundingAccountSummary(BaseModel):
    id: uuid.UUID
    name: str
    allocated_budget: Decimal
    total_income: Decimal
    total_expenses: Decimal
    balance: Decimal
    overspending: bool
    overspending_pct: Decimal | None


class TransactionCreate(BaseModel):
    type: TransactionType
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    category: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    over_budget_confirmed: bool = False


class TransactionResponse(BaseModel):
    id: uuid.UUID
    funding_account_id: uuid.UUID
    type: TransactionType
    amount: Decimal
    category: str
    description: str | None
    # Internal storage paths are never exposed; clients receive a boolean flag instead.
    has_invoice: bool = False
    recorded_by: uuid.UUID
    recorded_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _derive_has_invoice(cls, v):
        """Map invoice_attachment_path → has_invoice and drop the raw path."""
        if hasattr(v, "invoice_attachment_path"):
            # ORM object
            return {
                "id": v.id,
                "funding_account_id": v.funding_account_id,
                "type": v.type,
                "amount": v.amount,
                "category": v.category,
                "description": v.description,
                "has_invoice": bool(v.invoice_attachment_path),
                "recorded_by": v.recorded_by,
                "recorded_at": v.recorded_at,
            }
        if isinstance(v, dict) and "invoice_attachment_path" in v:
            v = dict(v)
            v["has_invoice"] = bool(v.pop("invoice_attachment_path", None))
        return v


class OverBudgetError(BaseModel):
    detail: str = "over_budget"
    current_total_expenses: Decimal
    allocated_budget: Decimal
    overage_pct: Decimal


class FinancialStatItem(BaseModel):
    category: str
    total_income: Decimal
    total_expense: Decimal


class FinancialStats(BaseModel):
    items: list[FinancialStatItem]
    grand_total_income: Decimal
    grand_total_expense: Decimal
