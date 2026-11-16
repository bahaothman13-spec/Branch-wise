from flask import Blueprint, jsonify, request

from supplier_validation import OPTIONAL_FIELDS, validate_supplier_input


supplier_bp = Blueprint("suppliers", __name__, url_prefix="/api")

ALLOWED_FIELDS = {"name", *OPTIONAL_FIELDS}


@supplier_bp.post("/suppliers")
def create_supplier():
    """Validate supplier requests without creating database records yet."""

    if not request.is_json:
        return jsonify({
            "error": "Request body must be JSON."
        }), 400

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Request body must be a JSON object."
        }), 400

    errors = validate_supplier_input(data)

    if set(data) - ALLOWED_FIELDS:
        errors["fields"] = "Unknown fields are not allowed."

    if errors:
        return jsonify({"errors": errors}), 400

    return jsonify({
        "message": (
            "Supplier creation is pending authentication "
            "and database integration."
        )
    }), 501
