from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.admin_ops import router as admin_ops_router
from app.api.v1.auth import router as auth_router
from app.api.v1.batches import router as batches_router
from app.api.v1.finance import router as finance_router
from app.api.v1.health import router as health_router
from app.api.v1.materials import router as materials_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.quality_validation import router as quality_validation_router
from app.api.v1.registrations import router as registrations_router
from app.api.v1.reports import router as reports_router
from app.api.v1.reviews import router as reviews_router
from app.config import settings

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(admin_ops_router, prefix="/admin", tags=["admin-ops"])
api_router.include_router(batches_router, prefix="/batches", tags=["batches"])
api_router.include_router(registrations_router, prefix="/registrations", tags=["registrations"])
api_router.include_router(materials_router, prefix="/registrations", tags=["materials"])
api_router.include_router(quality_validation_router, prefix="/registrations", tags=["quality-validation"])
api_router.include_router(reviews_router, prefix="/reviews", tags=["reviews"])
api_router.include_router(finance_router, prefix="/finance", tags=["finance"])
api_router.include_router(metrics_router, tags=["metrics"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])

# Conditionally register duplicate-check endpoint
if settings.ENABLE_DUPLICATE_CHECK_API:
    from app.api.v1.duplicates import router as duplicates_router
    api_router.include_router(duplicates_router, prefix="/materials", tags=["duplicates"])
