from flask import Blueprint

from response_helpers import error_response, json_response
from request_validation import (
    is_valid_email,
    password_length_error,
    read_json_object,
    required_string_error,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MIN_PASSWORD_LENGTH = 8


@auth_bp.post("/register")
def register():
    """Validate owner registration data without creating an account yet."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    required_fields = ("owner_name", "business_name", "email", "password")

    for field in required_fields:
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."

    password = data.get("password")
    password_error = password_length_error(password, MIN_PASSWORD_LENGTH)
    if password_error:
        errors["password"] = password_error

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {"message": "Input is valid, but registration is pending database integration."},
        status_code=501,
    )


@auth_bp.post("/login")
def login():
    """Validate login data until database authentication is implemented."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    for field in ("email", "password"):
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {"message": "Database authentication is not implemented yet."},
        status_code=501,
    )


@auth_bp.post("/logout")
def logout():
    """Prepare the logout endpoint until session management is implemented."""
    return json_response(
        {"message": "Logout is not available until session management is implemented."},
        status_code=501,
    )
