"""Maintenance mode middleware.

When /tmp/app_maintenance exists, all non-admin API requests receive 503.
"""
import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_MAINTENANCE_FILE = "/tmp/app_maintenance"


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if os.path.exists(_MAINTENANCE_FILE):
            # Allow admin endpoints and health check through
            path = request.url.path
            if not (path.startswith("/api/v1/admin") or path == "/api/v1/health"):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "System is in maintenance mode. Please try again later."},
                )
        return await call_next(request)
