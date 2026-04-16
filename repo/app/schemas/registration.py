import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.registration import RegistrationStatus
from app.models.user import UserRole
from app.utils.masking import mask_email, mask_id_number, mask_phone


class RegistrationCreate(BaseModel):
    batch_id: uuid.UUID
    title: str | None = Field(None, max_length=500)
    activity_type: str | None = Field(None, max_length=100)
    description: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    requested_budget: Decimal | None = Field(None, ge=0, decimal_places=2)
    applicant_name: str | None = Field(None, max_length=255)
    applicant_id_number: str | None = None
    applicant_phone: str | None = None
    applicant_email: str | None = None


class RegistrationDraftUpdate(BaseModel):
    """Partial update — all fields optional, structural validation only."""
    wizard_step: int | None = Field(None, ge=1)
    title: str | None = Field(None, max_length=500)
    activity_type: str | None = Field(None, max_length=100)
    description: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    requested_budget: Decimal | None = Field(None, ge=0, decimal_places=2)
    applicant_name: str | None = Field(None, max_length=255)
    applicant_id_number: str | None = None
    applicant_phone: str | None = None
    applicant_email: str | None = None


class RegistrationResponse(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    applicant_id: uuid.UUID
    status: RegistrationStatus
    wizard_step: int
    title: str | None
    activity_type: str | None
    description: str | None
    start_date: datetime | None
    end_date: datetime | None
    requested_budget: Decimal | None
    applicant_name: str | None
    applicant_id_number: str | None
    applicant_phone: str | None
    applicant_email: str | None
    supplementary_used: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    def mask_pii(self, role: UserRole, is_owner: bool) -> "RegistrationResponse":
        """Return a copy with PII masked based on role.

        Applicants see their own data unmasked. System admins see all.
        Everyone else sees masked PII.
        """
        if is_owner or role == UserRole.SYSTEM_ADMIN:
            return self
        return self.model_copy(update={
            "applicant_id_number": mask_id_number(self.applicant_id_number),
            "applicant_phone": mask_phone(self.applicant_phone),
            "applicant_email": mask_email(self.applicant_email),
        })


class RegistrationListItem(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    applicant_id: uuid.UUID
    status: RegistrationStatus
    title: str | None
    activity_type: str | None
    applicant_name: str | None
    requested_budget: Decimal | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginatedRegistrations(BaseModel):
    items: list[RegistrationListItem]
    total: int
    page: int
    page_size: int
