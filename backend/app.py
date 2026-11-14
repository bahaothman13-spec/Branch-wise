import os

from dotenv import load_dotenv
from flask import Flask, request
from sqlalchemy.engine import make_url # type: ignore
from werkzeug.exceptions import HTTPException

from routes.auth import auth_bp
from routes.account_settings import account_settings_bp
from routes.branches import branches_bp
from routes.health import health_bp
from routes.products import products_bp
from routes.users import users_bp
from response_helpers import error_response
from rate_limiting import LoginRateLimitConfig
from db import ENV_FILE, db, get_database_uri, migrate


DEFAULT_MAX_CONTENT_LENGTH = 1024 * 1024
MAX_CONFIGURABLE_CONTENT_LENGTH = 16 * 1024 * 1024


def _environment_boolean(name, default=False):
    """Parse a deployment Boolean explicitly instead of treating typos as false."""
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a Boolean value.")


def _environment_security_defaults():
    """Load supported deployment settings before constructing the app."""
    load_dotenv(dotenv_path=ENV_FILE)
    return {
        "APP_ENV": os.environ.get("APP_ENV", "development"),
        "SESSION_COOKIE_SECURE": _environment_boolean(
            "SESSION_COOKIE_SECURE", default=False
        ),
        "DEBUG": _environment_boolean("FLASK_DEBUG", default=False),
        "SECRET_KEY": os.environ.get("SECRET_KEY") or None,
    }


class BranchWiseFlask(Flask):
    """Flask app that never writes exception details or tracebacks to logs."""

    def log_exception(self, exc_info):
        # Flask normally logs the exception tuple, including a traceback. Avoid
        # recording request values, credentials, database errors, or secrets.
        self.logger.error("Unhandled request exception.")


def _validate_security_config(config):
    max_content_length = config["MAX_CONTENT_LENGTH"]
    if (
        type(max_content_length) is not int
        or not 1 <= max_content_length <= MAX_CONFIGURABLE_CONTENT_LENGTH
    ):
        raise ValueError(
            "MAX_CONTENT_LENGTH must be a positive integer no greater than 16 MiB."
        )

    LoginRateLimitConfig(
        identifier_limit=config["LOGIN_RATE_LIMIT_IDENTIFIER_LIMIT"],
        client_limit=config["LOGIN_RATE_LIMIT_CLIENT_LIMIT"],
        window_seconds=config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"],
    )

    app_environment = config["APP_ENV"]
    if not isinstance(app_environment, str) or app_environment not in {
        "development",
        "test",
        "production",
    }:
        raise ValueError("APP_ENV must be development, test, or production.")
    if type(config["SESSION_COOKIE_SECURE"]) is not bool:
        raise ValueError("SESSION_COOKIE_SECURE must be a Boolean value.")
    if type(config.get("DEBUG", False)) is not bool:
        raise ValueError("DEBUG must be a Boolean value.")
    if app_environment == "production" and not config["SESSION_COOKIE_SECURE"]:
        raise ValueError("SESSION_COOKIE_SECURE must be enabled in production.")
    if app_environment == "production" and config.get("DEBUG"):
        raise ValueError("DEBUG must be disabled in production.")
    if app_environment == "production":
        try:
            database_url = make_url(config["SQLALCHEMY_DATABASE_URI"])
        except Exception:
            raise ValueError(
                "Production requires a valid persistent database configuration."
            ) from None
        if database_url.drivername.startswith("sqlite") and (
            database_url.database in (None, "", ":memory:")
            or database_url.query.get("mode") == "memory"
        ):
            raise ValueError(
                "Production cannot use an in-memory SQLite database."
            )

        secret_key = config.get("SECRET_KEY")
        if isinstance(secret_key, str):
            secret_length = len(secret_key.encode("utf-8"))
        elif isinstance(secret_key, bytes):
            secret_length = len(secret_key)
        else:
            secret_length = 0
        if secret_length < 32:
            raise ValueError(
                "Production requires a SECRET_KEY containing at least 32 bytes."
            )


def create_app(config=None):
    """Create the Flask application and register its API routes."""
    environment_defaults = _environment_security_defaults()
    app = BranchWiseFlask(__name__)
    database_uri = (
        config["SQLALCHEMY_DATABASE_URI"]
        if config is not None and "SQLALCHEMY_DATABASE_URI" in config
        else get_database_uri()
    )
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=DEFAULT_MAX_CONTENT_LENGTH,
        **environment_defaults,
        LOGIN_RATE_LIMIT_IDENTIFIER_LIMIT=5,
        LOGIN_RATE_LIMIT_CLIENT_LIMIT=30,
        LOGIN_RATE_LIMIT_WINDOW_SECONDS=15 * 60,
        SQLALCHEMY_DATABASE_URI=database_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    )
    if config is not None:
        app.config.update(config)
    _validate_security_config(app.config)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(account_settings_bp)
    app.register_blueprint(branches_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(products_bp)

    @app.before_request
    def enforce_request_body_limit():
        """Read request bodies through Werkzeug's configured size-limited stream."""
        request.get_data(cache=True)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        """Return generic JSON for routing and HTTP errors without internals."""
        status_code = error.code or 500
        messages = {
            400: "Invalid request.",
            401: "Authentication is required.",
            403: "Permission denied.",
            404: "Resource not found.",
            405: "Method not allowed.",
            413: "Request body is too large.",
            409: "The request conflicts with the current resource state.",
            422: "Request validation failed.",
            501: "This operation is not implemented.",
        }
        return error_response(
            messages.get(status_code, "The request could not be completed."),
            status_code=status_code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(_error):
        """Hide exception details and credentials from API clients."""
        return error_response(
            "An unexpected server error occurred.", status_code=500
        )

    @app.after_request
    def add_security_headers(response):
        """Attach conservative browser protections to all API responses."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; object-src 'none'; form-action 'none'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=False)
