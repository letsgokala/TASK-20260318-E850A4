import os

from pydantic import model_validator
from pydantic_settings import BaseSettings

_PLACEHOLDER_SECRETS = {
    "change-me-to-a-random-64-char-string",
    "change-me-to-a-fernet-key",
    "change-me-to-a-real-fernet-key",
}


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://app_user:app_password@localhost:5432/eagle_point"
    SECRET_KEY: str = "change-me-to-a-random-64-char-string"
    SENSITIVE_FIELD_KEY: str = "change-me-to-a-fernet-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ENABLE_DUPLICATE_CHECK_API: bool = False

    # Lockout settings
    LOCKOUT_ATTEMPT_LIMIT: int = 10
    LOCKOUT_WINDOW_MINUTES: int = 5
    LOCKOUT_DURATION_MINUTES: int = 30

    # Set to "1" in test environments to skip secret validation
    TESTING: str = "0"

    # Fail-closed modes for critical observability paths. When set to "1"
    # (the default), a failure in the named subsystem propagates as a 500
    # instead of being silently swallowed — the audit previously flagged the
    # "silent success" behavior as a compliance gap. Operators who need the
    # API to remain available under audit/decryption/validation outages can
    # explicitly set these to "0" to restore fail-open behavior (e.g. for
    # non-regulated tenants or during incident mitigation).
    AUDIT_FAIL_CLOSED: str = "1"
    DECRYPT_FAIL_CLOSED: str = "1"
    VALIDATION_FAIL_CLOSED: str = "1"
    ALERT_FAIL_CLOSED: str = "1"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _reject_placeholder_secrets(self) -> "Settings":
        if self.TESTING == "1":
            return self
        errors = []
        if self.SECRET_KEY in _PLACEHOLDER_SECRETS:
            errors.append("SECRET_KEY is still set to the default placeholder value.")
        if self.SENSITIVE_FIELD_KEY in _PLACEHOLDER_SECRETS:
            errors.append("SENSITIVE_FIELD_KEY is still set to the default placeholder value.")
        if errors:
            raise ValueError(
                "Insecure default secrets detected. Set real values via environment variables "
                "or a .env file before starting the application.\n" + "\n".join(errors)
            )
        return self


settings = Settings()
