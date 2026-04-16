import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.models.user import UserRole

_PASSWORD_MIN_LENGTH = 12


class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        errors: list[str] = []
        if len(v) < _PASSWORD_MIN_LENGTH:
            errors.append(f"at least {_PASSWORD_MIN_LENGTH} characters")
        if not any(c.isupper() for c in v):
            errors.append("one uppercase letter")
        if not any(c.islower() for c in v):
            errors.append("one lowercase letter")
        if not any(c.isdigit() for c in v):
            errors.append("one digit")
        if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?`~" for c in v):
            errors.append("one special character")
        if errors:
            raise ValueError("Password must contain: " + ", ".join(errors))
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: UserRole
    is_active: bool
    locked_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResetPasswordRequest(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        return UserCreate.validate_password_complexity(v)
