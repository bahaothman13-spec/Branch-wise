"""Authenticated account and Owner business-settings APIs."""

from flask import Blueprint, current_app, g

from account_settings_rules import (
    BusinessSettingsRecord,
    MIN_PASSWORD_LENGTH,
    view_business_settings,
    view_personal_account,
    validate_business_settings_update,
    validate_password_change,
    validate_profile_update,
)
from permissions import BUSINESS_SETTINGS_MANAGE, ResourceScope
from isolation_rules import can_access_resource
from repository_contracts import (
    BUSINESS_SETTINGS_REPOSITORY_EXTENSION,
    USER_REPOSITORY_EXTENSION,
    AccountProfileUpdateData,
    BusinessSettingsUpdateData,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import (
    is_valid_email,
    password_length_error,
    read_json_object,
    required_string_error,
    validate_allowed_fields,
)
from response_helpers import error_response, json_response
from session_auth import require_authenticated_session


account_settings_bp = Blueprint("account_settings", __name__, url_prefix="/api")

_PROFILE_FIELDS = ("name", "email")
_BUSINESS_SETTINGS_FIELDS = ("business_name", "email")
_PASSWORD_FIELDS = ("current_password", "new_password", "confirm_password")
_MAX_PASSWORD_LENGTH = 1024


def _partial_text_errors(data, allowed_fields):
    errors = validate_allowed_fields(data, allowed_fields)
    if not data:
        errors["body"] = "At least one editable field is required."

    for field_name in allowed_fields:
        if field_name not in data:
            continue
        field_error = required_string_error(data, field_name)
        if field_error:
            errors[field_name] = field_error
        elif field_name == "email" and not is_valid_email(data[field_name]):
            errors[field_name] = "Enter a valid email address."

    return errors


def _repository_failure(error):
    if isinstance(error, RepositoryAuthorizationError):
        return error_response("Permission denied.", status_code=403)
    if isinstance(error, RepositoryNotFoundError):
        return error_response("Resource not found.", status_code=404)
    if isinstance(error, RepositoryConflictError):
        return error_response(
            "The request conflicts with the current resource state.", status_code=409
        )
    return error_response("The account change could not be completed.", status_code=500)


def _business_settings_allowed(actor):
    principal = actor.principal
    return can_access_resource(
        principal,
        BUSINESS_SETTINGS_MANAGE,
        ResourceScope(business_id=principal.business_id, branch_id=None),
    )


def _business_settings_data(view):
    return {
        "business_id": view.business_id,
        "business_name": view.business_name,
        "email": view.email,
    }


def _personal_account_data(view):
    return {
        "user_id": view.user_id,
        "name": view.name,
        "email": view.email,
        "role": view.role.value,
        "business_id": view.business_id,
        "branch_id": view.branch_id,
        "is_active": view.is_active,
    }


@account_settings_bp.get("/users/me")
@require_authenticated_session
def get_my_account():
    """Return only safe profile fields for the authenticated account."""
    repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response(
            "Account persistence is not available.", status_code=501
        )

    actor = g.authenticated_session
    try:
        account = repository.get_own_account(actor=actor)
        view = view_personal_account(actor.principal, actor.user_id, account)
    except PermissionError:
        return error_response("Resource not found or access denied.", status_code=403)
    except ValueError:
        return error_response("Account data could not be loaded.", status_code=500)
    except RepositoryError as error:
        return _repository_failure(error)

    return json_response({"data": _personal_account_data(view)})


@account_settings_bp.patch("/users/me")
@require_authenticated_session
def update_my_account():
    """Validate and persist name/email changes for the authenticated account."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = _partial_text_errors(data, _PROFILE_FIELDS)
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response(
            "Account persistence is not available.", status_code=501
        )

    actor = g.authenticated_session
    try:
        account = repository.get_own_account(actor=actor)
        proposal = validate_profile_update(
            actor.principal, actor.user_id, account, data
        )
        updated = repository.update_own_profile_atomic(
            actor=actor,
            data=AccountProfileUpdateData(proposal.changes),
        )
        view = view_personal_account(actor.principal, actor.user_id, updated)
    except PermissionError:
        return error_response("Resource not found or access denied.", status_code=403)
    except ValueError:
        return error_response("Account data could not be updated.", status_code=500)
    except RepositoryError as error:
        return _repository_failure(error)

    return json_response(
        {"data": _personal_account_data(view), "message": "Account updated."}
    )


@account_settings_bp.patch("/users/me/password")
@require_authenticated_session
def change_my_password():
    """Verify the current password and persist only its proposed secure hash."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = validate_allowed_fields(data, _PASSWORD_FIELDS)
    for field_name in _PASSWORD_FIELDS:
        field_error = required_string_error(data, field_name)
        if field_error:
            errors[field_name] = field_error

    new_password = data.get("new_password")
    length_error = password_length_error(new_password, MIN_PASSWORD_LENGTH)
    if length_error:
        errors["new_password"] = length_error
    for field_name in _PASSWORD_FIELDS:
        value = data.get(field_name)
        if isinstance(value, str) and len(value) > _MAX_PASSWORD_LENGTH:
            errors[field_name] = (
                f"Password must be no more than {_MAX_PASSWORD_LENGTH} characters long."
            )
    confirmation = data.get("confirm_password")
    if (
        isinstance(new_password, str)
        and new_password.strip()
        and isinstance(confirmation, str)
        and confirmation != new_password
    ):
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response(
            "Account persistence is not available.", status_code=501
        )

    actor = g.authenticated_session
    try:
        account = repository.get_own_account(actor=actor)
        proposal = validate_password_change(
            actor.principal,
            actor.user_id,
            account,
            current_password=data["current_password"],
            new_password=new_password,
            confirm_password=confirmation,
        )
        repository.change_own_password_atomic(
            actor=actor,
            new_password_hash=proposal.password_hash,
        )
    except PermissionError:
        return error_response("Current password is incorrect.", status_code=403)
    except ValueError:
        return error_response("Password change could not be completed.", status_code=500)
    except RepositoryError as error:
        return _repository_failure(error)

    return json_response({"message": "Password changed successfully."})


@account_settings_bp.get("/business/settings")
@require_authenticated_session
def get_business_settings():
    """Return business settings only to an Owner authorized for that business."""
    actor = g.authenticated_session
    if not _business_settings_allowed(actor):
        return error_response("Permission denied.", status_code=403)

    repository = current_app.extensions.get(BUSINESS_SETTINGS_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response(
            "Business settings persistence is not available.", status_code=501
        )

    try:
        business = repository.get_own_business_settings(actor=actor)
        view = view_business_settings(actor.principal, business)
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError:
        return error_response("Business settings could not be loaded.", status_code=500)
    except RepositoryError as error:
        return _repository_failure(error)

    return json_response({"data": _business_settings_data(view)})


@account_settings_bp.patch("/business/settings")
@require_authenticated_session
def update_business_settings():
    """Validate and persist approved business name/contact fields for an Owner."""
    actor = g.authenticated_session
    if not _business_settings_allowed(actor):
        return error_response("Permission denied.", status_code=403)

    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = _partial_text_errors(data, _BUSINESS_SETTINGS_FIELDS)
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(BUSINESS_SETTINGS_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response(
            "Business settings persistence is not available.", status_code=501
        )

    try:
        business = repository.get_own_business_settings(actor=actor)
        proposal = validate_business_settings_update(
            actor.principal, business, data
        )
        updated = repository.update_own_business_settings_atomic(
            actor=actor,
            data=BusinessSettingsUpdateData(proposal.changes),
        )
        if type(updated) is not BusinessSettingsRecord:
            raise ValueError("The repository returned invalid settings data.")
        view = view_business_settings(actor.principal, updated)
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError:
        return error_response("Business settings could not be updated.", status_code=500)
    except RepositoryError as error:
        return _repository_failure(error)

    return json_response(
        {"data": _business_settings_data(view), "message": "Business settings updated."}
    )


__all__ = ["account_settings_bp"]
