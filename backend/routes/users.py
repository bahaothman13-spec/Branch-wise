import re

from flask import Blueprint, jsonify, request


users_bp = Blueprint("users", __name__, url_prefix="/api")

MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_ROLES = {"Branch Manager", "Employee"}


@users_bp.post("/users")
def create_user():
    """Validate user creation data until authentication and database support exist."""
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON."}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    errors = {}
    string_fields = ("name", "email", "role", "initial_password")

    for field in string_fields:
        if field not in data:
            errors[field] = "This field is required."
            continue

        value = data[field]
        if not isinstance(value, str):
            errors[field] = "Must be a string."
        elif not value.strip():
            errors[field] = "This field is required."

    if "branch_id" not in data:
        errors["branch_id"] = "This field is required."
    elif type(data["branch_id"]) is not int:
        errors["branch_id"] = "Must be an integer."
    elif data["branch_id"] < 1:
        errors["branch_id"] = "Must be a positive integer."

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not EMAIL_PATTERN.fullmatch(email.strip()):
        errors["email"] = "Enter a valid email address."

    role = data.get("role")
    if isinstance(role, str) and role.strip() and role not in ALLOWED_ROLES:
        errors["role"] = "Role must be Branch Manager or Employee."

    password = data.get("initial_password")
    if isinstance(password, str) and password.strip() and len(password) < MIN_PASSWORD_LENGTH:
        errors["initial_password"] = (
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )

    if errors:
        return jsonify({"errors": errors}), 400

    return jsonify({
        "message": (
            "Input is valid, but user creation is unavailable until owner authentication "
            "and database support are implemented."
        )
    }), 501
