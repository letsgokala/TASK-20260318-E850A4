import logging
import mimetypes
import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.read_audit import audit_read
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.financial import FinancialTransaction, FundingAccount, TransactionType
from app.models.registration import Registration
from app.models.user import User, UserRole
from app.utils.emergency_log import record_critical_failure
from app.utils.file_validation import validate_file_content

logger = logging.getLogger(__name__)
from app.schemas.financial import (
    FinancialStatItem,
    FinancialStats,
    FundingAccountCreate,
    FundingAccountResponse,
    FundingAccountSummary,
    OverBudgetError,
    TransactionCreate,
    TransactionResponse,
)

router = APIRouter()

_finance_roles = require_roles(UserRole.FINANCIAL_ADMIN, UserRole.SYSTEM_ADMIN)
_INVOICE_STORAGE_ROOT = "/storage/invoices"
_ALLOWED_INVOICE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _safe_invoice_filename(original_filename: str | None, mime_type: str | None = None) -> str:
    """Return a safe, server-generated filename for invoice uploads."""
    import uuid as _uuid
    ext = ""
    if original_filename:
        base = os.path.basename(original_filename)
        _, raw_ext = os.path.splitext(base)
        if raw_ext.lower() in _ALLOWED_INVOICE_EXTENSIONS:
            ext = raw_ext.lower()
    if not ext and mime_type:
        _mime_ext_map = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}
        ext = _mime_ext_map.get(mime_type, "")
    return f"{_uuid.uuid4().hex}{ext}"


def _assert_invoice_path_under_root(resolved_path: str, root: str) -> None:
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(resolved_path)
    if not real_path.startswith(real_root + os.sep) and real_path != real_root:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invoice upload path.",
        )


# ── Funding account CRUD ───────────────────────────────────────────────────

@router.post("/accounts", response_model=FundingAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_funding_account(
    body: FundingAccountCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
):
    # Verify registration exists
    reg_result = await db.execute(
        select(Registration).where(Registration.id == body.registration_id)
    )
    if not reg_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    account = FundingAccount(
        registration_id=body.registration_id,
        name=body.name,
        allocated_budget=body.allocated_budget,
        created_by=current_user.id,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.get("/accounts", response_model=list[FundingAccountResponse])
async def list_funding_accounts(
    registration_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
    _audit: None = Depends(audit_read("funding_account", "list")),
):
    query = select(FundingAccount).order_by(FundingAccount.created_at.desc())
    if registration_id:
        query = query.where(FundingAccount.registration_id == registration_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/accounts/{account_id}", response_model=FundingAccountSummary)
async def get_funding_account_summary(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
    _audit: None = Depends(audit_read("funding_account", "detail", "account_id")),
):
    return await _build_account_summary(account_id, db)


# ── Transactions ───────────────────────────────────────────────────────────

@router.post(
    "/accounts/{account_id}/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    account_id: uuid.UUID,
    body: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
):
    # Row-level lock on the funding account to prevent race conditions
    lock_result = await db.execute(
        select(FundingAccount)
        .where(FundingAccount.id == account_id)
        .with_for_update()
    )
    account = lock_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funding account not found")

    # Over-budget check for expense transactions
    if body.type == TransactionType.EXPENSE:
        total_expenses_result = await db.execute(
            select(func.coalesce(func.sum(FinancialTransaction.amount), Decimal("0")))
            .where(
                FinancialTransaction.funding_account_id == account.id,
                FinancialTransaction.type == TransactionType.EXPENSE,
            )
        )
        current_expenses = total_expenses_result.scalar_one()
        projected = current_expenses + body.amount
        threshold = account.allocated_budget * Decimal("1.10")

        if projected > threshold and not body.over_budget_confirmed:
            overage_pct = (
                (projected - account.allocated_budget) / account.allocated_budget * 100
                if account.allocated_budget > 0
                else Decimal("100")
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=OverBudgetError(
                    current_total_expenses=projected,
                    allocated_budget=account.allocated_budget,
                    overage_pct=round(overage_pct, 2),
                ).model_dump(mode="json"),
            )

    txn = FinancialTransaction(
        funding_account_id=account.id,
        type=body.type,
        amount=body.amount,
        category=body.category,
        description=body.description,
        recorded_by=current_user.id,
    )
    db.add(txn)

    # Evaluate overspending thresholds BEFORE committing so the transaction
    # and any resulting alert notifications land atomically. If alert
    # emission fails under ALERT_FAIL_CLOSED=1, the raise here aborts the
    # commit — closing the prior gap where a committed finance transaction
    # could outlive a failed alert.
    from app.api.v1.metrics import check_and_notify_breaches
    await check_and_notify_breaches(db)

    await db.commit()
    await db.refresh(txn)

    return txn


@router.get(
    "/accounts/{account_id}/transactions",
    response_model=list[TransactionResponse],
)
async def list_transactions(
    account_id: uuid.UUID,
    category: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
    _audit: None = Depends(audit_read("financial_transaction", "list", "account_id")),
):
    # Verify account exists
    acct_result = await db.execute(
        select(FundingAccount).where(FundingAccount.id == account_id)
    )
    if not acct_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funding account not found")

    query = (
        select(FinancialTransaction)
        .where(FinancialTransaction.funding_account_id == account_id)
        .order_by(FinancialTransaction.recorded_at.desc())
    )
    if category:
        query = query.where(FinancialTransaction.category == category)
    if from_date:
        query = query.where(FinancialTransaction.recorded_at >= from_date)
    if to_date:
        query = query.where(FinancialTransaction.recorded_at <= to_date)

    result = await db.execute(query)
    return result.scalars().all()


# ── Invoice attachment upload ──────────────────────────────────────────────

@router.post(
    "/transactions/{transaction_id}/invoice",
    response_model=TransactionResponse,
)
async def upload_invoice(
    transaction_id: uuid.UUID,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
):
    txn_result = await db.execute(
        select(FinancialTransaction).where(FinancialTransaction.id == transaction_id)
    )
    txn = txn_result.scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    content = await file.read()

    # Validate declared MIME + magic-byte signature (defence-in-depth).
    validate_file_content(content, file.content_type, context="invoice")

    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Invoice file exceeds 20MB",
        )

    storage_dir = os.path.join(_INVOICE_STORAGE_ROOT, str(txn.funding_account_id))
    os.makedirs(storage_dir, exist_ok=True)
    safe_filename = f"{txn.id}_{_safe_invoice_filename(file.filename, file.content_type)}"
    storage_path = os.path.join(storage_dir, safe_filename)
    _assert_invoice_path_under_root(storage_path, _INVOICE_STORAGE_ROOT)

    with open(storage_path, "wb") as f:
        f.write(content)

    txn.invoice_attachment_path = storage_path
    await db.commit()
    await db.refresh(txn)
    return txn


# ── Invoice download (sensitive read, transactional audit) ────────────────

@router.get("/transactions/{transaction_id}/invoice")
async def download_invoice(
    transaction_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
):
    """Stream the uploaded invoice attachment for a transaction.

    The audit report flagged invoice files as write-only evidence because
    there was no retrieval path — uploaded files lived on disk but could
    not be inspected via the API (only a ``has_invoice`` boolean was
    exposed). This endpoint closes that gap with the same transactional
    audit contract as material/report downloads: the download audit row
    is committed before the file is streamed, and a commit failure under
    ``AUDIT_FAIL_CLOSED=1`` blocks the download with a 500.
    """
    txn_result = await db.execute(
        select(FinancialTransaction).where(FinancialTransaction.id == transaction_id)
    )
    txn = txn_result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found",
        )

    if not txn.invoice_attachment_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No invoice attached to this transaction",
        )

    if not os.path.isfile(txn.invoice_attachment_path):
        # Path recorded in DB but file missing — integrity issue.
        logger.error(
            "Invoice file missing on disk",
            extra={
                "transaction_id": str(txn.id),
                "storage_path": txn.invoice_attachment_path,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice file missing on disk",
        )

    # Defence-in-depth: confirm the resolved path is still under the
    # invoice storage root to prevent any future path-traversal slip.
    real_root = os.path.realpath(_INVOICE_STORAGE_ROOT)
    real_path = os.path.realpath(txn.invoice_attachment_path)
    if not (real_path.startswith(real_root + os.sep) or real_path == real_root):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid invoice storage path",
        )

    # Stage and commit the download audit row BEFORE streaming the file.
    audit_entry = AuditLog(
        user_id=current_user.id,
        action=f"DOWNLOAD invoice/{transaction_id}",
        resource_type="invoice",
        resource_id=transaction_id,
        details={
            "transaction_id": str(transaction_id),
            "funding_account_id": str(txn.funding_account_id),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(audit_entry)
    fallback_header: dict[str, str] = {}
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(
            "Invoice download audit write failed",
            extra={
                "transaction_id": str(transaction_id),
                "user_id": str(current_user.id),
            },
            exc_info=True,
        )
        record_critical_failure(
            category="audit_download_invoice",
            message="failed to persist invoice download audit row",
            transaction_id=str(transaction_id),
            user_id=str(current_user.id),
            error=repr(exc),
        )
        from app.config import settings as _settings
        if _settings.AUDIT_FAIL_CLOSED == "1":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Audit log persistence failed; download blocked "
                    "(AUDIT_FAIL_CLOSED=1)."
                ),
            ) from exc
        fallback_header = {"X-Audit-Log-Fallback": "emergency-log"}

    # Derive a friendly filename from the stored path. The upload flow
    # already prefixes with the transaction id and the extension comes
    # from the safe-filename helper, so the extension is trustworthy.
    stored_name = os.path.basename(txn.invoice_attachment_path)
    _, ext = os.path.splitext(stored_name)
    friendly = f"invoice_{transaction_id}{ext or ''}"
    media_type, _ = mimetypes.guess_type(txn.invoice_attachment_path)
    return FileResponse(
        txn.invoice_attachment_path,
        filename=friendly,
        media_type=media_type or "application/octet-stream",
        headers=fallback_header or None,
    )


# ── Financial statistics ───────────────────────────────────────────────────

@router.get("/statistics", response_model=FinancialStats)
async def get_financial_statistics(
    category: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_finance_roles),
    _audit: None = Depends(audit_read("finance_statistics", "aggregate")),
):
    income_sum = func.coalesce(
        func.sum(
            case(
                (FinancialTransaction.type == TransactionType.INCOME, FinancialTransaction.amount),
                else_=Decimal("0"),
            )
        ),
        Decimal("0"),
    )
    expense_sum = func.coalesce(
        func.sum(
            case(
                (FinancialTransaction.type == TransactionType.EXPENSE, FinancialTransaction.amount),
                else_=Decimal("0"),
            )
        ),
        Decimal("0"),
    )

    query = select(
        FinancialTransaction.category,
        income_sum.label("total_income"),
        expense_sum.label("total_expense"),
    ).group_by(FinancialTransaction.category)

    if category:
        query = query.where(FinancialTransaction.category == category)
    if from_date:
        query = query.where(FinancialTransaction.recorded_at >= from_date)
    if to_date:
        query = query.where(FinancialTransaction.recorded_at <= to_date)

    result = await db.execute(query)
    rows = result.all()

    items = [
        FinancialStatItem(category=r.category, total_income=r.total_income, total_expense=r.total_expense)
        for r in rows
    ]

    grand_income = sum(i.total_income for i in items)
    grand_expense = sum(i.total_expense for i in items)

    return FinancialStats(
        items=items,
        grand_total_income=grand_income,
        grand_total_expense=grand_expense,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _build_account_summary(account_id: uuid.UUID, db: AsyncSession) -> FundingAccountSummary:
    result = await db.execute(select(FundingAccount).where(FundingAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funding account not found")

    income_result = await db.execute(
        select(func.coalesce(func.sum(FinancialTransaction.amount), Decimal("0")))
        .where(
            FinancialTransaction.funding_account_id == account.id,
            FinancialTransaction.type == TransactionType.INCOME,
        )
    )
    total_income = income_result.scalar_one()

    expense_result = await db.execute(
        select(func.coalesce(func.sum(FinancialTransaction.amount), Decimal("0")))
        .where(
            FinancialTransaction.funding_account_id == account.id,
            FinancialTransaction.type == TransactionType.EXPENSE,
        )
    )
    total_expenses = expense_result.scalar_one()

    available_funds = account.allocated_budget + total_income
    balance = available_funds - total_expenses
    overspending = total_expenses > available_funds
    overspending_pct = (
        round((total_expenses - available_funds) / available_funds * 100, 2)
        if overspending and available_funds > 0
        else None
    )

    return FundingAccountSummary(
        id=account.id,
        name=account.name,
        allocated_budget=account.allocated_budget,
        total_income=total_income,
        total_expenses=total_expenses,
        balance=balance,
        overspending=overspending,
        overspending_pct=overspending_pct,
    )
