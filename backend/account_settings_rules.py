"""Pure account and business-settings rules for trusted in-memory records.

These functions authorize and validate proposed changes only. Callers must
build the principal, authenticated user ID, and records from verified
server-side data. They do not load records, persist updates, authenticate
requests, or create sessions.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from isolation_rules import can_access_resource
from permissions import (
    BUSINESS_SETTINGS_MANAGE,
    PASSWORD_CHANGE_SELF,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    is_trusted_principal,
)
from request_validation import (
    is_positive_integer,
    is_valid_email,
    password_length_error,
    required_string_error,
)
from security import hash_password, verify_password


MIN_PASSWORD_LENGTH = 8
_BUSINESS_SETTINGS_FIELDS = frozenset({"business_name", "email"})
_PROFILE_FIELDS = frozenset({"name", "email"})


@dataclass(frozen=True)
class BusinessSettingsRecord:
    """Business settings loaded by the server for the stated business."""

    business_id: int
    business_name: str
    email: str


@dataclass(frozen=True)
class BusinessSettingsView:
    """The approved business name and existing registration contact email."""

    business_id: int
    business_name: str
    email: str


@dataclass(frozen=True)
class BusinessSettingsProposal:
    """Normalized settings fields proposed for an authorized business."""

    changes: Mapping[str, str]


@dataclass(frozen=True)
class AccountRecord:
    """Current account row loaded by the server; the hash is never displayed."""

    user_id: int
    business_id: int
    name: str
    email: str
    role: Role
    branch_id: int | None
    is_active: bool
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class PersonalAccountView:
    """Permitted self-account information, excluding credentials and secrets."""

    user_id: int
    name: str
    email: str
    role: Role
    business_id: int
    branch_id: int | None
    is_active: bool


@dataclass(frozen=True)
class ProfileUpdateProposal:
    """Normalized personal fields proposed for the authenticated account."""

    changes: Mapping[str, str]


@dataclass(frozen=True)
class PasswordChangeProposal:
    """Secure password update proposal; only a password hash is retained."""

    password_hash: str = field(repr=False)


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_text_changes(changes, allowed_fields, subject):
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError(f"At least one {subject} change is required.")
    if set(changes) - allowed_fields:
        raise ValueError(f"{subject.capitalize()} changes contain unsupported fields.")

    normalized = {}
    errors = {}
    for field_name in changes:
        error = required_string_error(changes, field_name)
        if error:
            errors[field_name] = error
            continue
        value = changes[field_name].strip()
        if field_name == "email" and not is_valid_email(value):
            errors[field_name] = "Enter a valid email address."
            continue
        normalized[field_name] = value

    if errors:
        messages = "; ".join(
            f"{name}: {message}" for name, message in errors.items()
        )
        raise ValueError(messages)
    return MappingProxyType(normalized)


def _validate_business_record(business):
    if type(business) is not BusinessSettingsRecord:
        raise ValueError("A trusted business settings record is required.")
    if not is_positive_integer(business.business_id):
        raise ValueError("The trusted business has an invalid business_id.")
    if not _is_nonempty_string(business.business_name):
        raise ValueError("The trusted business has an invalid business_name.")
    if not is_valid_email(business.email):
        raise ValueError("The trusted business has an invalid email.")


def _require_business_owner(principal, business):
    if not is_trusted_principal(principal):
        raise PermissionError("Resource not found or access denied.")
    _validate_business_record(business)
    scope = ResourceScope(business_id=business.business_id, branch_id=None)
    if (
        principal.role is not Role.OWNER
        or not can_access_resource(principal, BUSINESS_SETTINGS_MANAGE, scope)
    ):
        raise PermissionError("Resource not found or access denied.")


def view_business_settings(principal, business):
    """Return approved settings for the authorized Owner's own business."""
    _require_business_owner(principal, business)
    return BusinessSettingsView(
        business_id=business.business_id,
        business_name=business.business_name.strip(),
        email=business.email.strip(),
    )


def validate_business_settings_update(principal, business, changes):
    """Validate an Owner's partial settings update without persisting it."""
    _require_business_owner(principal, business)
    return BusinessSettingsProposal(
        changes=_validate_text_changes(
            changes, _BUSINESS_SETTINGS_FIELDS, "business settings"
        )
    )


def _validate_account_context(principal, authenticated_user_id, account):
    if not is_trusted_principal(principal):
        raise PermissionError("Resource not found or access denied.")
    if type(account) is not AccountRecord:
        raise ValueError("A trusted account record is required.")
    if not is_positive_integer(account.user_id):
        raise ValueError("The trusted account has an invalid user_id.")
    if not is_positive_integer(account.business_id):
        raise ValueError("The trusted account has an invalid business_id.")
    if not _is_nonempty_string(account.name):
        raise ValueError("The trusted account has an invalid name.")
    if not is_valid_email(account.email):
        raise ValueError("The trusted account has an invalid email.")
    if type(account.role) is not Role:
        raise ValueError("The trusted account has an invalid role.")
    if type(account.is_active) is not bool:
        raise ValueError("The trusted account has an invalid is_active value.")
    if type(account.password_hash) is not str or not account.password_hash:
        raise ValueError("The trusted account has an invalid password hash.")
    if account.role is Role.OWNER:
        if account.branch_id is not None:
            raise ValueError("The trusted Owner account has an invalid branch_id.")
    elif not is_positive_integer(account.branch_id):
        raise ValueError("The trusted account has an invalid branch_id.")

    if not is_positive_integer(authenticated_user_id):
        raise PermissionError("Resource not found or access denied.")
    if (
        authenticated_user_id != account.user_id
        or account.business_id != principal.business_id
        or account.role is not principal.role
        or account.branch_id != principal.branch_id
    ):
        raise PermissionError("Resource not found or access denied.")


def view_personal_account(principal, authenticated_user_id, account):
    """Return safe account fields for the authenticated user only."""
    _validate_account_context(principal, authenticated_user_id, account)
    return PersonalAccountView(
        user_id=account.user_id,
        name=account.name.strip(),
        email=account.email.strip(),
        role=account.role,
        business_id=account.business_id,
        branch_id=account.branch_id,
        is_active=account.is_active,
    )


def validate_profile_update(principal, authenticated_user_id, account, changes):
    """Validate editable name/email fields for the authenticated user only."""
    _validate_account_context(principal, authenticated_user_id, account)
    return ProfileUpdateProposal(
        changes=_validate_text_changes(changes, _PROFILE_FIELDS, "profile")
    )


def validate_password_change(
    principal,
    authenticated_user_id,
    account,
    *,
    current_password,
    new_password,
    confirm_password,
):
    """Verify and hash a proposed self-service password change in memory."""
    _validate_account_context(principal, authenticated_user_id, account)
    scope = ResourceScope(account.business_id, account.branch_id)
    if not can_access_resource(principal, PASSWORD_CHANGE_SELF, scope):
        raise PermissionError("You are not allowed to change this account password.")

    data = {
        "current_password": current_password,
        "new_password": new_password,
        "confirm_password": confirm_password,
    }
    errors = {}
    for field_name in data:
        error = required_string_error(data, field_name)
        if error:
            errors[field_name] = error

    length_error = password_length_error(new_password, MIN_PASSWORD_LENGTH)
    if length_error:
        errors["new_password"] = length_error
    if (
        isinstance(new_password, str)
        and new_password.strip()
        and isinstance(confirm_password, str)
        and confirm_password != new_password
    ):
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        messages = "; ".join(
            f"{name}: {message}" for name, message in errors.items()
        )
        raise ValueError(messages)

    try:
        password_matches = verify_password(current_password, account.password_hash)
    except (TypeError, ValueError):
        raise ValueError("The trusted account has an invalid password hash.") from None
    if not password_matches:
        raise PermissionError("Current password is incorrect.")

    new_hash = hash_password(new_password)
    if not isinstance(new_hash, str) or not new_hash or new_hash == new_password:
        raise ValueError("Secure password hashing failed.")
    return PasswordChangeProposal(password_hash=new_hash)


__all__ = [
    "AccountRecord",
    "BusinessSettingsProposal",
    "BusinessSettingsRecord",
    "BusinessSettingsView",
    "MIN_PASSWORD_LENGTH",
    "PasswordChangeProposal",
    "PersonalAccountView",
    "ProfileUpdateProposal",
    "validate_business_settings_update",
    "validate_password_change",
    "validate_profile_update",
    "view_business_settings",
    "view_personal_account",
]
