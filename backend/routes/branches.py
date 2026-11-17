"""Owner-only branch management APIs."""

from flask import Blueprint, current_app, g

from isolation_rules import can_access_resource
from permissions import BRANCHES_MANAGE, ResourceScope
from repository_contracts import (
    BRANCH_REPOSITORY_EXTENSION,
    BranchCreateData,
    BranchRecord,
    BranchUpdateData,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import parse_positive_integer_id, read_json_object, validate_allowed_fields
from response_helpers import error_response, json_response
from session_auth import require_authenticated_session


branches_bp = Blueprint("branches", __name__, url_prefix="/api/branches")

_OPTIONAL_STRING_FIELDS = ("address", "phone", "notes")
_EDITABLE_FIELDS = ("name", *_OPTIONAL_STRING_FIELDS)


def _owner_allowed(actor):
    principal = actor.principal
    return can_access_resource(
        principal,
        BRANCHES_MANAGE,
        ResourceScope(principal.business_id, None),
    )


def _repository():
    return current_app.extensions.get(BRANCH_REPOSITORY_EXTENSION)


def _failure(error):
    if isinstance(error, RepositoryAuthorizationError):
        return error_response("Permission denied.", status_code=403)
    if isinstance(error, RepositoryNotFoundError):
        return error_response("Resource not found.", status_code=404)
    if isinstance(error, RepositoryConflictError):
        return error_response(
            "The request conflicts with the current resource state.", status_code=409
        )
    return error_response("The branch operation could not be completed.", status_code=500)


def _branch_json(branch):
    return {
        "branch_id": branch.branch_id,
        "business_id": branch.business_id,
        "name": branch.name,
        "address": branch.address,
        "phone": branch.phone,
        "notes": branch.notes,
        "is_active": branch.is_active,
    }


def _branch_in_scope(branch, actor, *, branch_id=None):
    if type(branch) is not BranchRecord:
        raise RepositoryError("The repository returned invalid branch data.")
    if (
        branch.business_id != actor.principal.business_id
        or (branch_id is not None and branch.branch_id != branch_id)
    ):
        raise RepositoryNotFoundError("Branch is outside the actor's business.")
    return branch


def _branch_body(*, partial):
    data, json_error = read_json_object()
    if json_error:
        return None, error_response(json_error, status_code=400)
    fields = _EDITABLE_FIELDS
    errors = validate_allowed_fields(data, fields)
    if partial and not data:
        errors["body"] = "Provide at least one branch field to update."
    if "name" not in data:
        if not partial:
            errors["name"] = "This field is required."
    elif not isinstance(data["name"], str):
        errors["name"] = "Must be a string."
    elif not data["name"].strip():
        errors["name"] = "Branch name cannot be empty."
    for field in _OPTIONAL_STRING_FIELDS:
        if field in data and not isinstance(data[field], str):
            errors[field] = "Must be a string."
    if errors:
        return None, error_response(errors=errors, status_code=400)
    normalized = dict(data)
    for field, value in normalized.items():
        normalized[field] = value.strip()
    return normalized, None


@branches_bp.get("")
@require_authenticated_session
def list_branches():
    actor = g.authenticated_session
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    repository = _repository()
    if repository is None:
        return error_response("Branch persistence is not available.", status_code=501)
    try:
        branches = repository.list_branches(actor=actor)
        if type(branches) not in (tuple, list):
            raise RepositoryError("The repository returned invalid branches.")
        rows = [_branch_in_scope(branch, actor) for branch in branches]
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": [_branch_json(branch) for branch in rows]})


@branches_bp.post("")
@require_authenticated_session
def create_branch():
    actor = g.authenticated_session
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _branch_body(partial=False)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Branch persistence is not available.", status_code=501)
    try:
        created = _branch_in_scope(
            repository.create_branch_atomic(
                actor=actor,
                data=BranchCreateData(
                    name=data["name"],
                    address=data.get("address"),
                    phone=data.get("phone"),
                    notes=data.get("notes"),
                ),
            ),
            actor,
        )
    except ValueError as error:
        return error_response(errors={"body": str(error)}, status_code=400)
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _branch_json(created), "message": "Branch created."},
        status_code=201,
    )


@branches_bp.get("/<branch_id>")
@require_authenticated_session
def get_branch(branch_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(branch_id)
    if numeric_id is None:
        return error_response(
            errors={"branch_id": "Must be a positive integer."}, status_code=400
        )
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    repository = _repository()
    if repository is None:
        return error_response("Branch persistence is not available.", status_code=501)
    try:
        branch = _branch_in_scope(
            repository.get_branch(actor=actor, branch_id=numeric_id),
            actor,
            branch_id=numeric_id,
        )
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": _branch_json(branch)})


@branches_bp.patch("/<branch_id>")
@require_authenticated_session
def update_branch(branch_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(branch_id)
    if numeric_id is None:
        return error_response(
            errors={"branch_id": "Must be a positive integer."}, status_code=400
        )
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _branch_body(partial=True)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Branch persistence is not available.", status_code=501)
    try:
        updated = _branch_in_scope(
            repository.update_branch_atomic(
                actor=actor,
                branch_id=numeric_id,
                data=BranchUpdateData(data),
            ),
            actor,
            branch_id=numeric_id,
        )
    except ValueError as error:
        return error_response(errors={"body": str(error)}, status_code=400)
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _branch_json(updated), "message": "Branch updated."}
    )


@branches_bp.patch("/<branch_id>/deactivate")
@require_authenticated_session
def deactivate_branch(branch_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(branch_id)
    if numeric_id is None:
        return error_response(
            errors={"branch_id": "Must be a positive integer."}, status_code=400
        )
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    repository = _repository()
    if repository is None:
        return error_response("Branch persistence is not available.", status_code=501)
    try:
        branch = _branch_in_scope(
            repository.deactivate_branch_atomic(actor=actor, branch_id=numeric_id),
            actor,
            branch_id=numeric_id,
        )
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _branch_json(branch), "message": "Branch deactivated."}
    )


__all__ = ["branches_bp"]
