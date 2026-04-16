import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator

from app.models.export_task import ExportStatus


class ExportTaskResponse(BaseModel):
    id: uuid.UUID
    report_type: str
    status: ExportStatus
    # file_path is intentionally omitted — internal server path must not be exposed to clients.
    # Use the /tasks/{id}/download endpoint to retrieve the completed file.
    error_message: str | None
    created_by: uuid.UUID
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _strip_file_path(cls, v):
        """Drop the internal file_path field before validation so it never reaches clients."""
        if isinstance(v, dict):
            v = {k: val for k, val in v.items() if k != "file_path"}
        return v


class DuplicateMatch(BaseModel):
    version_id: uuid.UUID
    material_id: uuid.UUID
    registration_id: uuid.UUID
    original_filename: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}
