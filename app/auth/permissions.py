from app.models.user import UserRole

PERMISSION_MATRIX: dict[UserRole, list[str]] = {
    UserRole.APPLICANT: [
        "registration:create",
        "registration:read_own",
        "registration:update_own",
        "registration:cancel_own",
        "material:upload_own",
        "material:read_own",
    ],
    UserRole.REVIEWER: [
        "registration:read_all",
        "review:create",
        "review:batch",
        "review:read_all",
        "material:read_all",
    ],
    UserRole.FINANCIAL_ADMIN: [
        "registration:read_metadata",
        "finance:create",
        "finance:read_all",
        "finance:statistics",
        "report:export",
    ],
    UserRole.SYSTEM_ADMIN: [
        "admin:user_manage",
        "admin:backup",
        "admin:restore",
        "admin:audit_read",
        "admin:integrity_check",
        "admin:threshold_manage",
        "registration:read_all",
        "review:create",
        "review:batch",
        "review:read_all",
        "material:read_all",
        "finance:create",
        "finance:read_all",
        "finance:statistics",
        "report:export",
    ],
}
