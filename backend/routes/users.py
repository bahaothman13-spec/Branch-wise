"""Owner-only staff account management APIs."""

from flask import Blueprint, current_app, g

from auth_workflows import normalize_email
from isolation_rules import can_access_resource
from permissions import BRANCHES_MANAGE, USERS_MANAGE, ResourceScope, Role
from repository_contracts import (
    BRANCH_REPOSITORY_EXTENSION,
    USER_REPOSITORY_EXTENSION,
    BranchRecord,
    UserCreateData,
    UserRecord,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import (
    is_valid_email,
    parse_positive_integer_id,
    password_length_error,
    read_json_object,
    required_string_error,
    validate_allowed_fields,
)
from response_helpers import error_response, json_response
from security import hash_password
from session_auth import require_authenticated_session


users_bp = Blueprint("users", __name__, url_prefix="/api")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
ALLOWED_STAFF_ROLES = {
    Role.BRANCH_MANAGER.value: Role.BRANCH_MANAGER,
    Role.EMPLOYEE.value: Role.EMPLOYEE,
}


def _owner_allowed(actor):
    principal = actor.principal
    return can_access_resource(
        principal,
        USERS_MANAGE,
        ResourceScope(principal.business_id, None),
    )


def _failure(error, subject="user"):
    if isinstance(error, RepositoryAuthorizationError):
        return error_response("Permission denied.", status_code=403)
    if isinstance(error, RepositoryNotFoundError):
        return error_response("Resource not found.", status_code=404)
    if isinstance(error, RepositoryConflictError):
        return error_response(
            "The request conflicts with the current resource state.", status_code=409
        )
    return error_response(f"The {subject} operation could not be completed.", status_code=500)


def _user_json(user):
    return {
        "user_id": user.user_id,
        "name": user.full_name,
        "email": user.email,
        "role": user.role.value,
        "business_id": user.business_id,
        "branch_id": user.branch_id,
        "is_active": user.is_active,
    }


def _user_in_scope(user, actor, *, user_id=None):
    if type(user) is not UserRecord:
        raise RepositoryError("The repository returned invalid user data.")
    if (
        user.business_id != actor.principal.business_id
        or (user_id is not None and user.user_id != user_id)
        or user.role is Role.OWNER
    ):
        raise RepositoryNotFoundError("User is outside the actor's staff scope.")
    return user


def _branch_in_scope(branch, actor, *, branch_id):
    if type(branch) is not BranchRecord:
        raise RepositoryError("The repository returned invalid branch data.")
    if (
        branch.branch_id != branch_id
        or branch.business_id != actor.principal.business_id
    ):
        raise RepositoryNotFoundError("Branch is outside the actor's business.")
    if not branch.is_active:
        raise RepositoryConflictError("The branch is inactive.")
    return branch


@users_bp.get("/users")
@require_authenticated_session
def list_users():
    """List staff in the authenticated Owner's business."""
    actor = g.authenticated_session
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response("User persistence is not available.", status_code=501)
    try:
        users = repository.list_users(actor=actor)
        if type(users) not in (tuple, list):
            raise RepositoryError("The repository returned invalid users.")
        rows = [_user_in_scope(user, actor) for user in users]
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": [_user_json(user) for user in rows]})


@users_bp.post("/users")
@require_authenticated_session
def create_user():
    """Create a Branch Manager or Employee scoped to the Owner's business."""
    actor = g.authenticated_session
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    allowed = ("name", "email", "role", "branch_id", "initial_password")
    errors = validate_allowed_fields(data, allowed)
    for field in ("name", "email", "role", "initial_password"):
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error
    if "branch_id" not in data:
        errors["branch_id"] = "This field is required."
    elif type(data["branch_id"]) is not int or data["branch_id"] <= 0:
        errors["branch_id"] = "Must be a positive integer."

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."
    role = data.get("role")
    if isinstance(role, str) and role.strip() and role not in ALLOWED_STAFF_ROLES:
        errors["role"] = "Role must be Branch Manager or Employee."
    password = data.get("initial_password")
    password_error = password_length_error(password, MIN_PASSWORD_LENGTH)
    if password_error:
        errors["initial_password"] = password_error
    if isinstance(password, str) and len(password) > MAX_PASSWORD_LENGTH:
        errors["initial_password"] = (
            f"Password must be no more than {MAX_PASSWORD_LENGTH} characters long."
        )
    if errors:
        return error_response(errors=errors, status_code=400)

    user_repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    branch_repository = current_app.extensions.get(BRANCH_REPOSITORY_EXTENSION)
    if user_repository is None or branch_repository is None:
        return error_response("User and branch persistence is not available.", status_code=501)
    try:
        branch = _branch_in_scope(
            branch_repository.get_branch(actor=actor, branch_id=data["branch_id"]),
            actor,
            branch_id=data["branch_id"],
        )
        del branch
        request_data = UserCreateData(
            full_name=data["name"].strip(),
            email=normalize_email(data["email"]),
            role=ALLOWED_STAFF_ROLES[data["role"]],
            branch_id=data["branch_id"],
            password_hash=hash_password(password),
        )
        user = _user_in_scope(
            user_repository.create_user_atomic(actor=actor, data=request_data), actor
        )
    except ValueError as error:
        return error_response(errors={"body": str(error)}, status_code=400)
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _user_json(user), "message": "User created."}, status_code=201
    )


@users_bp.patch("/users/<user_id>")
@require_authenticated_session
def assign_user(user_id):
    """Change a staff member's role and branch as one authorized operation."""
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(user_id)
    if numeric_id is None:
        return error_response(
            errors={"user_id": "Must be a positive integer."}, status_code=400
        )
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, ("role", "branch_id"))
    if "role" not in data:
        errors["role"] = "This field is required."
    elif not isinstance(data["role"], str) or data["role"] not in ALLOWED_STAFF_ROLES:
        errors["role"] = "Role must be Branch Manager or Employee."
    if "branch_id" not in data:
        errors["branch_id"] = "This field is required."
    elif type(data["branch_id"]) is not int or data["branch_id"] <= 0:
        errors["branch_id"] = "Must be a positive integer."
    if errors:
        return error_response(errors=errors, status_code=400)

    user_repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    branch_repository = current_app.extensions.get(BRANCH_REPOSITORY_EXTENSION)
    if user_repository is None or branch_repository is None:
        return error_response("User and branch persistence is not available.", status_code=501)
    try:
        _branch_in_scope(
            branch_repository.get_branch(actor=actor, branch_id=data["branch_id"]),
            actor,
            branch_id=data["branch_id"],
        )
        user = _user_in_scope(
            user_repository.assign_user_atomic(
                actor=actor,
                user_id=numeric_id,
                role=ALLOWED_STAFF_ROLES[data["role"]],
                branch_id=data["branch_id"],
            ),
            actor,
            user_id=numeric_id,
        )
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _user_json(user), "message": "User assignment updated."}
    )


@users_bp.patch("/users/<user_id>/status")
@require_authenticated_session
def update_user_status(user_id):
    """Enable or disable a same-business staff account."""
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(user_id)
    if numeric_id is None:
        return error_response(
            errors={"user_id": "Must be a positive integer."}, status_code=400
        )
    if not _owner_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, ("is_active",))
    if "is_active" not in data:
        errors["is_active"] = "This field is required."
    elif type(data["is_active"]) is not bool:
        errors["is_active"] = "Must be a Boolean value (true or false)."
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(USER_REPOSITORY_EXTENSION)
    if repository is None:
        return error_response("User persistence is not available.", status_code=501)
    try:
        user = _user_in_scope(
            repository.set_user_active_atomic(
                actor=actor, user_id=numeric_id, is_active=data["is_active"]
            ),
            actor,
            user_id=numeric_id,
        )
    except RepositoryError as error:
        return _failure(error)
    return json_response(
        {"data": _user_json(user), "message": "User status updated."}
    )


__all__ = ["users_bp"]
