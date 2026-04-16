from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.auth.password import verify_password
from app.config import settings
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Look up user
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if user is None:
        # Log failed attempt without user_id
        db.add(AuditLog(
            action="login_failed",
            details={"reason": "unknown_username", "username": body.username},
            ip_address=ip,
            user_agent=user_agent,
        ))
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated",
        )

    # Check if account is locked
    now = datetime.now(timezone.utc)
    locked = user.locked_until
    if locked is not None:
        # Ensure tz-aware comparison (SQLite stores naive datetimes)
        if locked.tzinfo is None:
            locked = locked.replace(tzinfo=timezone.utc)
    if locked and locked > now:
        remaining = int((locked - now).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is locked due to too many failed login attempts",
            headers={"Retry-After": str(remaining)},
        )

    # Verify password
    if not verify_password(body.password, user.password_hash):
        # Record failed attempt and flush so count query includes it
        db.add(LoginAttempt(user_id=user.id, success=False))
        await db.flush()

        # Count recent failures (includes the just-flushed attempt)
        window_start = datetime.now(timezone.utc) - timedelta(
            minutes=settings.LOCKOUT_WINDOW_MINUTES
        )
        count_result = await db.execute(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.user_id == user.id,
                LoginAttempt.success == False,
                LoginAttempt.attempted_at > window_start,
            )
        )
        fail_count = count_result.scalar_one()

        if fail_count >= settings.LOCKOUT_ATTEMPT_LIMIT:
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=settings.LOCKOUT_DURATION_MINUTES
            )

        db.add(AuditLog(
            user_id=user.id,
            action="login_failed",
            details={"reason": "bad_password", "fail_count": fail_count},
            ip_address=ip,
            user_agent=user_agent,
        ))
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Successful login — record, clear lock, and purge failed attempts
    db.add(LoginAttempt(user_id=user.id, success=True))
    if user.locked_until:
        user.locked_until = None

    # Clear prior failed attempts so they don't count toward future lockouts
    from sqlalchemy import delete
    await db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.user_id == user.id,
            LoginAttempt.success == False,
        )
    )

    db.add(AuditLog(
        user_id=user.id,
        action="login_success",
        ip_address=ip,
        user_agent=user_agent,
    ))
    await db.commit()

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return TokenResponse(access_token=token)
