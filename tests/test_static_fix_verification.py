"""Static regression checks for previously audited fixes.

These tests avoid the async database harness so they can still validate
route/schema regressions even when the DB-backed fixtures cannot run
(e.g. Postgres not available locally).
"""

from app.main import app
from app.schemas.material import MaterialVersionResponse
from app.schemas.report import ExportTaskResponse


def test_reports_generation_route_uses_post_generate_prefix():
    matching = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/reports/generate/{report_type}"
    ]

    assert matching, "Expected /api/v1/reports/generate/{report_type} to be registered"
    assert all("POST" in route.methods for route in matching)
    assert all("GET" not in route.methods for route in matching)


def test_reports_tasks_route_is_registered_separately():
    matching = [
        route for route in app.routes if getattr(route, "path", None) == "/api/v1/reports/tasks"
    ]

    assert matching, "Expected /api/v1/reports/tasks to be registered"
    assert all("GET" in route.methods for route in matching)


def test_material_version_response_hides_internal_fingerprint_fields():
    fields = MaterialVersionResponse.model_fields

    assert "sha256_hash" not in fields
    assert "uploaded_by" not in fields


def test_export_task_response_hides_internal_file_path():
    assert "file_path" not in ExportTaskResponse.model_fields
