from flask import Blueprint, jsonify, request


branches_bp = Blueprint("branches", __name__, url_prefix="/api/branches")

OPTIONAL_STRING_FIELDS = ("address", "phone", "notes")


@branches_bp.post("")
def create_branch():
    """Validate branch data until Owner authentication and database support exist."""
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON."}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    errors = {}

    if "name" not in data:
        errors["name"] = "This field is required."
    elif not isinstance(data["name"], str):
        errors["name"] = "Must be a string."
    elif not data["name"].strip():
        errors["name"] = "This field is required."

    for field in OPTIONAL_STRING_FIELDS:
        if field in data and not isinstance(data[field], str):
            errors[field] = "Must be a string."

    if errors:
        return jsonify({"errors": errors}), 400

    # Once implemented, require an authenticated Owner and use that Owner's
    # trusted business_id. The database must reject duplicate names in that
    # business before creating the branch.
    return jsonify({
        "message": (
            "Input is valid, but branch creation is unavailable until Owner "
            "authentication and database integration are implemented."
        )
    }), 501
