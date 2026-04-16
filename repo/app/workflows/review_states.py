"""Review workflow state machine.

Each key is a current status. Each value is a list of
(target_status, allowed_roles) tuples.
"""
from app.models.registration import RegistrationStatus
from app.models.user import UserRole

_R = UserRole.REVIEWER
_A = UserRole.APPLICANT
_S = UserRole.SYSTEM_ADMIN

VALID_TRANSITIONS: dict[RegistrationStatus, list[tuple[RegistrationStatus, set[UserRole]]]] = {
    RegistrationStatus.SUBMITTED: [
        (RegistrationStatus.APPROVED, {_R, _S}),
        (RegistrationStatus.REJECTED, {_R, _S}),
        (RegistrationStatus.WAITLISTED, {_R, _S}),
        (RegistrationStatus.CANCELED, {_A, _R, _S}),
    ],
    RegistrationStatus.SUPPLEMENTED: [
        (RegistrationStatus.APPROVED, {_R, _S}),
        (RegistrationStatus.REJECTED, {_R, _S}),
        (RegistrationStatus.WAITLISTED, {_R, _S}),
        (RegistrationStatus.CANCELED, {_A, _R, _S}),
    ],
    RegistrationStatus.WAITLISTED: [
        (RegistrationStatus.PROMOTED_FROM_WAITLIST, {_R, _S}),
        (RegistrationStatus.CANCELED, {_R, _S}),
    ],
    RegistrationStatus.APPROVED: [
        (RegistrationStatus.CANCELED, {_R, _S}),
    ],
    RegistrationStatus.PROMOTED_FROM_WAITLIST: [
        (RegistrationStatus.CANCELED, {_R, _S}),
    ],
    # Terminal states — no transitions out
    RegistrationStatus.REJECTED: [],
    RegistrationStatus.CANCELED: [],
    RegistrationStatus.DRAFT: [],  # Draft uses submit, not the review transition
}


def get_allowed_targets(
    current: RegistrationStatus, role: UserRole
) -> list[RegistrationStatus]:
    """Return the list of statuses the given role can transition to from `current`."""
    transitions = VALID_TRANSITIONS.get(current, [])
    return [target for target, roles in transitions if role in roles]


def is_valid_transition(
    current: RegistrationStatus, target: RegistrationStatus, role: UserRole
) -> bool:
    return target in get_allowed_targets(current, role)
