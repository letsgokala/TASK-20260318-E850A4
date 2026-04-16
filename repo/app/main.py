from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.middleware.audit import AuditMiddleware
from app.middleware.maintenance import MaintenanceMiddleware

app = FastAPI(
    title="Activity Registration & Funding Audit Platform",
    version="0.1.0",
)

# Maintenance mode checked first (outermost middleware runs first in Starlette)
app.add_middleware(AuditMiddleware)
app.add_middleware(MaintenanceMiddleware)

# API routes are registered first so they take priority over static files
app.include_router(api_router, prefix="/api/v1")

# Serve the Vue SPA frontend from frontend/dist/ if it exists
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIR.is_dir():
    # Mount static assets (JS, CSS, images, etc.)
    _ASSETS_DIR = _FRONTEND_DIR / "assets"
    if _ASSETS_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="frontend-assets")

    # Serve other static files at the root (favicon, vite.svg, etc.)
    @app.get("/vite.svg")
    async def vite_svg():
        svg_path = _FRONTEND_DIR / "vite.svg"
        if svg_path.is_file():
            return FileResponse(str(svg_path), media_type="image/svg+xml")

    # Catch-all route: serve index.html for any non-API, non-asset path (SPA routing)
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # If the requested file exists in dist/, serve it directly
        requested_file = _FRONTEND_DIR / full_path
        if full_path and requested_file.is_file():
            return FileResponse(str(requested_file))
        # Otherwise serve index.html for client-side routing
        index_file = _FRONTEND_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return FileResponse(str(index_file))
