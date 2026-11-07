"""API-level tests for BE-51 Part 1 security foundations."""

import pytest

from app import create_app


PRODUCTION_DATABASE_URI = (
    "mysql+mysqlconnector://test_user:test_password@db.example.test/branchwise"
)


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/api/auth/login", "application/json"),
        ("/api/auth/logout", "text/plain"),
    ],
)
def test_oversized_request_returns_consistent_json_413(path, content_type):
    app = create_app({"MAX_CONTENT_LENGTH": 128})

    response = app.test_client().post(
        path,
        data=b"x" * 200,
        content_type=content_type,
    )

    assert response.status_code == 413
    assert response.is_json
    assert response.get_json() == {"error": "Request body is too large."}


def test_security_headers_are_attached_to_success_and_error_responses():
    client = create_app().test_client()
    expected = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'none'; object-src 'none'; form-action 'none'; "
            "frame-ancestors 'none'; base-uri 'none'"
        ),
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cache-Control": "no-store",
    }

    for response in (client.get("/api/health"), client.get("/api/unknown")):
        for header, value in expected.items():
            assert response.headers[header] == value


def test_api_does_not_enable_cors_for_an_untrusted_origin():
    response = create_app().test_client().get(
        "/api/health", headers={"Origin": "https://untrusted.example"}
    )

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_unknown_input_key_that_looks_like_a_secret_is_not_reflected():
    private_key = "csrf-token-private-value"
    response = create_app().test_client().post(
        "/api/branches", json={"name": "Main", private_key: "ignored"}
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"body": "This field is not allowed."}
    }
    assert private_key not in response.get_data(as_text=True)


def test_unexpected_secrets_and_stack_trace_are_not_returned_or_logged(caplog):
    app = create_app({"TESTING": True})

    @app.get("/api/test-log-protection")
    def raise_sensitive_error():
        raise RuntimeError(
            "password=private-pass token=private-token "
            "csrf=private-csrf DB_PASSWORD=private-db-secret STACK_MARKER"
        )

    response = app.test_client().get("/api/test-log-protection")
    exposed_text = response.get_data(as_text=True) + caplog.text

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "An unexpected server error occurred."
    }
    for secret in (
        "private-pass",
        "private-token",
        "private-csrf",
        "private-db-secret",
        "STACK_MARKER",
        "Traceback",
    ):
        assert secret not in exposed_text
    assert caplog.text == ""


@pytest.mark.parametrize(
    "config",
    [
        {"MAX_CONTENT_LENGTH": 0},
        {"MAX_CONTENT_LENGTH": True},
        {"MAX_CONTENT_LENGTH": 16 * 1024 * 1024 + 1},
        {"LOGIN_RATE_LIMIT_IDENTIFIER_LIMIT": False},
        {"LOGIN_RATE_LIMIT_CLIENT_LIMIT": 0},
        {"LOGIN_RATE_LIMIT_WINDOW_SECONDS": 1.5},
        {"APP_ENV": "prod"},
        {"SESSION_COOKIE_SECURE": "true"},
        {"APP_ENV": "production", "SESSION_COOKIE_SECURE": False},
        {"APP_ENV": "production", "SESSION_COOKIE_SECURE": True, "DEBUG": True},
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "short",
            "SQLALCHEMY_DATABASE_URI": PRODUCTION_DATABASE_URI,
        },
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": None,
            "SQLALCHEMY_DATABASE_URI": PRODUCTION_DATABASE_URI,
        },
        {"DEBUG": "false"},
    ],
)
def test_invalid_security_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        create_app(config)


def test_valid_production_security_configuration_is_accepted():
    app = create_app(
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "k" * 32,
            "SQLALCHEMY_DATABASE_URI": PRODUCTION_DATABASE_URI,
        }
    )

    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_production_configuration_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("FLASK_DEBUG", "false")
    monkeypatch.setenv("SECRET_KEY", "production-secret-key-" + "k" * 32)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE_URI)

    app = create_app()

    assert app.config["APP_ENV"] == "production"
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["DEBUG"] is False
    assert app.config["SECRET_KEY"].startswith("production-secret-key-")


def test_production_rejects_in_memory_database_fallback():
    with pytest.raises(ValueError, match="in-memory SQLite"):
        create_app(
            {
                "APP_ENV": "production",
                "SESSION_COOKIE_SECURE": True,
                "DEBUG": False,
                "SECRET_KEY": "k" * 32,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            }
        )


def test_invalid_boolean_environment_setting_fails_startup(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "sometimes")

    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        create_app()


def test_password_length_is_bounded_before_authentication_or_registration():
    client = create_app().test_client()
    long_password = "p" * 1025

    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": long_password},
    )
    registration = client.post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": "owner@example.com",
            "password": long_password,
        },
    )

    assert login.status_code == registration.status_code == 400
    assert login.get_json()["errors"]["password"] == (
        "Password must be no more than 1024 characters long."
    )
    assert registration.get_json()["errors"]["password"] == (
        "Password must be no more than 1024 characters long."
    )


def test_environment_configured_production_without_adapters_denies_login(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("FLASK_DEBUG", "false")
    monkeypatch.setenv("SECRET_KEY", "k" * 32)
    monkeypatch.setenv("DATABASE_URL", PRODUCTION_DATABASE_URI)
    client = create_app().test_client()

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "valid-password"},
    )

    assert response.status_code == 501
    assert "Set-Cookie" not in response.headers
    assert response.get_json() == {
        "message": "Database authentication is not implemented yet."
    }


@pytest.mark.parametrize(
    ("method", "path", "payload", "field"),
    [
        (
            "post",
            "/api/auth/register",
            {
                "owner_name": "Owner",
                "business_name": "Business",
                "email": "owner@example.com",
                "password": "valid-password",
                "is_owner": True,
            },
            "is_owner",
        ),
        (
            "post",
            "/api/auth/login",
            {"email": "owner@example.com", "password": "valid-password", "role": "Owner"},
            "role",
        ),
        (
            "post",
            "/api/branches",
            {"name": "Branch", "business_id": 9},
            "business_id",
        ),
        (
            "patch",
            "/api/branches/2",
            {"name": "Branch", "owner_id": 7},
            "owner_id",
        ),
        (
            "post",
            "/api/users",
            {
                "name": "Worker",
                "email": "worker@example.com",
                "role": "Employee",
                "initial_password": "valid-password",
                "branch_id": 2,
                "business_id": 9,
            },
            "business_id",
        ),
        (
            "patch",
            "/api/users/3/status",
            {"is_active": True, "role": "Owner"},
            "role",
        ),
        (
            "patch",
            "/api/users/me/password",
            {
                "current_password": "current-password",
                "new_password": "replacement-password",
                "confirm_password": "replacement-password",
                "user_id": 3,
            },
            "user_id",
        ),
    ],
)
def test_json_routes_reject_non_allowlisted_fields(method, path, payload, field):
    response = getattr(create_app().test_client(), method)(path, json=payload)

    assert response.status_code == 400
    assert "body" in response.get_json()["errors"]
    assert field not in response.get_data(as_text=True)


def test_valid_placeholder_routes_keep_existing_status_and_payloads():
    client = create_app().test_client()
    valid_login = {"email": "owner@example.com", "password": "valid-password"}
    responses = [
        client.get("/api/health"),
        client.post("/api/auth/login", json=valid_login),
        client.post("/api/auth/logout"),
        client.post("/api/branches", json={"name": "Main"}),
    ]

    assert responses[0].status_code == 200
    assert responses[0].get_json() == {"status": "ok"}
    assert responses[1].status_code == 501
    assert responses[1].get_json() == {
        "message": "Database authentication is not implemented yet."
    }
    assert responses[2].status_code == 501
    assert responses[3].status_code == 501

    app = create_app()
    assert "branchwise.rate_limiter" not in app.extensions
