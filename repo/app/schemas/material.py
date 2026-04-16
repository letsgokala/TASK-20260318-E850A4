import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.material import MaterialVersionStatus


class MaterialResponse(BaseModel):
    id: uuid.UUID
    registration_id: uuid.UUID
    checklist_item_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class MaterialVersionResponse(BaseModel):
    """Outward-facing view of a material version.

    Internal fingerprint fields (``sha256_hash``, ``uploaded_by``) are
    intentionally omitted; they are server-side metadata used for duplicate
    detection, integrity checks, and audit logs. Exposing them over the API
    would leak user attribution and file fingerprints beyond what the
    business flow requires.
    """
    id: uuid.UUID
    material_id: uuid.UUID
    version_number: int
    original_filename: str
    mime_type: str
    file_size_bytes: int
    status: MaterialVersionStatus
    correction_reason: str | None
    duplicate_flag: bool
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class MaterialWithVersions(BaseModel):
    id: uuid.UUID
    registration_id: uuid.UUID
    checklist_item_id: uuid.UUID
    created_at: datetime
    versions: list[MaterialVersionResponse]

    model_config = {"from_attributes": True}


class MaterialStatusUpdate(BaseModel):
    status: MaterialVersionStatus
    correction_reason: str | None = None


class UploadSizeInfo(BaseModel):
    used_bytes: int
    limit_bytes: int
    remaining_bytes: int
    supplementary_eligible: bool
    supplementary_used: bool
