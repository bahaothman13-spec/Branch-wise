from flask import Blueprint

from response_helpers import error_response, json_response
from request_validation import (
    is_positive_integer,
    is_valid_email,
    password_length_error,
    parse_positive_integer_id,
    read_json_object,
    required_string_error,
)


users_bp = Blueprint("users", __name__, url_prefix="/api")

MIN_PASSWORD_LENGTH = 8
ALLOWED_ROLES = {"Branch Manager", "Employee"}


@users_bp.post("/users")
def create_user():
    """Validate user creation data until authentication and database support exist."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    string_fields = ("name", "email", "role", "initial_password")

    for field in string_fields:
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    if "branch_id" not in data:
        errors["branch_id"] = "This field is required."
    elif type(data["branch_id"]) is not int:
        errors["branch_id"] = "Must be an integer."
    elif not is_positive_integer(data["branch_id"]):
        errors["branch_id"] = "Must be a positive integer."

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."

    role = data.get("role")
    if isinstance(role, str) and role.strip() and role not in ALLOWED_ROLES:
        errors["role"] = "Role must be Branch Manager or Employee."

    password = data.get("initial_password")
    password_error = password_length_error(password, MIN_PASSWORD_LENGTH)
    if password_error:
        errors["initial_password"] = password_error

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {
            "message": (
                "Input is valid, but user creation is unavailable until owner authentication "
                "and database support are implemented."
            )
        },
        status_code=501,
    )


@users_bp.patch("/users/<user_id>/status")
def update_user_status(user_id):
    """Validate a status update until authentication and database support exist."""
    errors = {}

    if parse_positive_integer_id(user_id) is None:
        errors["user_id"] = "Must be a positive integer."

    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    if "is_active" not in data:
        errors["is_active"] = "This field is required."
    elif type(data["is_active"]) is not bool:
        errors["is_active"] = "Must be a Boolean value (true or false)."

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {
            "message": (
                "Input is valid, but changing user status is unavailable until authentication "
                "and database integration are implemented."
            )
        },
        status_code=501,
    )


@users_bp.patch("/users/me/password")
def change_my_password():
    """Validate password-change data until authentication and database support exist."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    password_fields = ("current_password", "new_password", "confirm_password")

    for field in password_fields:
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    new_password = data.get("new_password")
    password_error = password_length_error(new_password, MIN_PASSWORD_LENGTH)
    if password_error:
        errors["new_password"] = password_error

    if isinstance(new_password, str) and new_password.strip():
        confirm_password = data.get("confirm_password")
        if isinstance(confirm_password, str) and confirm_password != new_password:
            errors["confirm_password"] = "Passwords do not match."

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {
            "message": (
                "Input is valid, but changing passwords is unavailable until authentication "
                "and database integration are implemented."
            )
        },
        status_code=501,
    )
