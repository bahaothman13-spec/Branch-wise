from flask import Blueprint, current_app, jsonify, request

from auth_workflows import (
    AUTH_REPOSITORY_EXTENSION,
    AuthPersistenceError,
    DuplicateEmailError,
    InvalidCredentialsError,
    authenticate_user,
    register_owner,
)
from rate_limiting import LOGIN_RATE_LIMIT_EXTENSION
from response_helpers import error_response, json_response
from request_validation import (
    is_valid_email,
    password_length_error,
    read_json_object,
    required_string_error,
    validate_allowed_fields,
)
from session_auth import (
    SESSION_AUTH_EXTENSION,
    SESSION_COOKIE_NAME,
    clear_session_cookies,
    require_authenticated_session,
    set_session_cookies,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024


def _password_length_errors(errors, password):
    """Bound password work while retaining the existing field-error shape."""
    password_error = password_length_error(password, MIN_PASSWORD_LENGTH)
    if password_error:
        errors["password"] = password_error
    elif isinstance(password, str) and len(password) > MAX_PASSWORD_LENGTH:
        errors["password"] = (
            f"Password must be no more than {MAX_PASSWORD_LENGTH} characters long."
        )


@auth_bp.post("/register")
def register():
    """Validate owner registration data and use an installed repository."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    required_fields = ("owner_name", "business_name", "email", "password")
    errors.update(validate_allowed_fields(data, required_fields))

    for field in required_fields:
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."

    password = data.get("password")
    _password_length_errors(errors, password)

    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(AUTH_REPOSITORY_EXTENSION)
    if repository is not None:
        try:
            register_owner(
                repository,
                owner_name=data["owner_name"],
                business_name=data["business_name"],
                email=data["email"],
                password=data["password"],
            )
        except DuplicateEmailError:
            return error_response(
                "An account with this email already exists.", status_code=409
            )
        except AuthPersistenceError:
            return error_response(
                "Registration could not be completed.", status_code=500
            )

        return json_response(
            {
                "data": {
                    "owner_created": True,
                    "business_created": True,
                    "branch_created": False,
                },
                "message": "Registration successful.",
            },
            status_code=201,
        )

    return json_response(
        {"message": "Input is valid, but registration is pending database integration."},
        status_code=501,
    )


@auth_bp.post("/login")
def login():
    """Verify credentials only when repository and session persistence exist."""
    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)

    errors = {}
    allowed_fields = ("email", "password")
    errors.update(validate_allowed_fields(data, allowed_fields))
    for field in allowed_fields:
        field_error = required_string_error(data, field)
        if field_error:
            errors[field] = field_error

    _password_length_errors(errors, data.get("password"))

    email = data.get("email")
    if isinstance(email, str) and email.strip() and not is_valid_email(email):
        errors["email"] = "Enter a valid email address."

    if errors:
        return error_response(errors=errors, status_code=400)

    repository = current_app.extensions.get(AUTH_REPOSITORY_EXTENSION)
    session_manager = current_app.extensions.get(SESSION_AUTH_EXTENSION)
    rate_limiter = current_app.extensions.get(LOGIN_RATE_LIMIT_EXTENSION)
    if (
        repository is not None
        and session_manager is not None
        and rate_limiter is not None
    ):
        try:
            decision = rate_limiter.check(data["email"], request.remote_addr or "")
        except Exception:
            # The limiter is fail-closed: credentials are not checked if its
            # shared store is unavailable or returns an invalid decision.
            return error_response(
                "Authentication service is unavailable.", status_code=503
            )
        if not decision.allowed:
            response, status_code = error_response(
                "Too many login attempts. Try again later.", status_code=429
            )
            if decision.retry_after_seconds > 0:
                response.headers["Retry-After"] = str(decision.retry_after_seconds)
            return response, status_code

        try:
            outcome = authenticate_user(
                repository,
                session_manager,
                email=data["email"],
                password=data["password"],
            )
        except InvalidCredentialsError:
            return error_response("Invalid email or password.", status_code=401)
        except AuthPersistenceError:
            return error_response(
                "Authentication service is unavailable.", status_code=500
            )

        onboarding = {"required": outcome.onboarding.required}
        if outcome.onboarding.message is not None:
            onboarding["message"] = outcome.onboarding.message
        response = jsonify(
            {
                "data": {"onboarding": onboarding},
                "message": "Login successful.",
            }
        )
        set_session_cookies(
            response,
            outcome.issued_session,
            production=current_app.config["SESSION_COOKIE_SECURE"],
        )
        return response, 200

    return json_response(
        {"message": "Database authentication is not implemented yet."},
        status_code=501,
    )


@auth_bp.post("/logout")
def logout():
    """Revoke a valid CSRF-protected session when persistence is installed."""
    if current_app.extensions.get(SESSION_AUTH_EXTENSION) is None:
        return json_response(
            {"message": "Logout is not available until session management is implemented."},
            status_code=501,
        )
    return _logout_authenticated()


@require_authenticated_session
def _logout_authenticated():
    manager = current_app.extensions[SESSION_AUTH_EXTENSION]
    token = request.cookies.get(SESSION_COOKIE_NAME)
    try:
        revoked = manager.revoke_session(token)
    except Exception:
        return error_response("Logout could not be completed.", status_code=500)
    if not revoked:
        return error_response("Authentication is required.", status_code=401)

    response = jsonify({"message": "Logout successful."})
    clear_session_cookies(
        response,
        production=current_app.config["SESSION_COOKIE_SECURE"],
    )
    return response, 200
