from flask import Blueprint

from response_helpers import error_response, json_response
from request_validation import parse_positive_integer_id, read_json_object


branches_bp = Blueprint("branches", __name__, url_prefix="/api/branches")

OPTIONAL_STRING_FIELDS = ("address", "phone", "notes")
EDITABLE_FIELDS = ("name", *OPTIONAL_STRING_FIELDS)


def _branches_not_ready(action):
    return json_response(
        {
            "message": (
                f"Input is valid, but branch {action} is unavailable until Owner "
                "authentication and database integration are implemented."
            )
        },
        status_code=501,
    )


@branches_bp.get("")
def list_branches():
    """List the authenticated Owner's branches once auth and DB are ready."""
    # When implemented, load branches using the business_id from the verified
    # Owner principal. Never accept a business_id from the request.
    return _branches_not_ready("listing")


@branches_bp.get("/<branch_id>")
def get_branch(branch_id):
    """View one branch after validating its path ID."""
    if parse_positive_integer_id(branch_id) is None:
        return error_response(
            errors={"branch_id": "Must be a positive integer."}, status_code=400
        )

    # Load this branch within the authenticated Owner's business so another
    # business's branch cannot be viewed.
    return _branches_not_ready("details")


@branches_bp.patch("/<branch_id>/deactivate")
def deactivate_branch(branch_id):
    """Validate a branch deactivation request until auth and DB are ready."""
    if parse_positive_integer_id(branch_id) is None:
        return error_response(
            errors={"branch_id": "Must be a positive integer."}, status_code=400
        )

    # When implemented, require an authenticated Owner, load the branch within
    # that Owner's business, and set is_active to false. Keep its historical
    # sales, purchases, and inventory records; reject new sales and stock
    # transfers for inactive branches.
    return _branches_not_ready("deactivation")


@branches_bp.patch("/<branch_id>")
def update_branch(branch_id):
    """Validate editable branch fields until auth and database support exist."""
    errors = {}
    if parse_positive_integer_id(branch_id) is None:
        errors["branch_id"] = "Must be a positive integer."

    data, json_error = read_json_object()
    if json_error:
        if errors:
            errors["body"] = json_error
            return error_response(errors=errors, status_code=400)
        return error_response(json_error, status_code=400)

    if not data:
        errors["body"] = "Provide at least one field to update."

    for field in data:
        if field not in EDITABLE_FIELDS:
            errors[field] = "This field cannot be updated."

    if "name" in data:
        if not isinstance(data["name"], str):
            errors["name"] = "Must be a string."
        elif not data["name"].strip():
            errors["name"] = "Branch name cannot be empty."

    for field in OPTIONAL_STRING_FIELDS:
        if field in data and not isinstance(data[field], str):
            errors[field] = "Must be a string."

    if errors:
        return error_response(errors=errors, status_code=400)

    # When the DB is added, update by branch_id AND the verified Owner's
    # business_id, and check name uniqueness within that same business.
    return _branches_not_ready("updates")


@branches_bp.post("")
def create_branch():
    """Validate branch data until Owner authentication and database support exist."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

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
        return error_response(errors=errors, status_code=400)

    # Once implemented, require an authenticated Owner and use that Owner's
    # trusted business_id. The database must reject duplicate names in that
    # business before creating the branch.
    return json_response(
        {
            "message": (
                "Input is valid, but branch creation is unavailable until Owner "
                "authentication and database integration are implemented."
            )
        },
        status_code=501,
    )
