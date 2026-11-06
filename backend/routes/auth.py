import re

from flask import Blueprint, request

from response_helpers import error_response, json_response


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@auth_bp.post("/register")
def register():
    """Validate owner registration data without creating an account yet."""
    if not request.is_json:
        return error_response("Request body must be JSON.", status_code=400)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Request body must be a JSON object.", status_code=400)

    errors = {}
    required_fields = ("owner_name", "business_name", "email", "password")

    for field in required_fields:
        if field not in data:
            errors[field] = "This field is required."
            continue

        value = data.get(field)
        if not isinstance(value, str):
            errors[field] = "Must be a string."
        elif not value.strip():
            errors[field] = "This field is required."

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not EMAIL_PATTERN.fullmatch(email.strip()):
        errors["email"] = "Enter a valid email address."

    password = data.get("password")
    if isinstance(password, str) and password.strip() and len(password) < MIN_PASSWORD_LENGTH:
        errors["password"] = f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."

    if errors:
        return error_response(errors=errors, status_code=400)

    return json_response(
        {"message": "Input is valid, but registration is pending database integration."},
        status_code=501,
    )


@auth_bp.post("/login")
def login():
    """Validate login data until database authentication is implemented."""
    if not request.is_json:
        return error_response("Request body must be JSON.", status_code=400)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Request body must be a JSON object.", status_code=400)

    errors = {}
    for field in ("email", "password"):
        if field not in data:
            errors[field] = "This field is required."
            continue

        value = data.get(field)
        if not isinstance(value, str):
            errors[field] = "Must be a string."
        elif not value.strip():
            errors[field] = "This field is required."

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not EMAIL_PATTERN.fullmatch(email.strip()):
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
