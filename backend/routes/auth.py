import re

from flask import Blueprint, jsonify, request


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@auth_bp.post("/register")
def register():
    """Validate owner registration data without creating an account yet."""
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON."}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

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
        return jsonify({"errors": errors}), 400

    return jsonify({
        "message": "Input is valid, but registration is pending database integration."
    }), 501
