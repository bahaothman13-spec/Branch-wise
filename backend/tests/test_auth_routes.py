"""Route integration tests with injected test-local auth and session stores."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from flask import current_app, g, jsonify

from app import create_app
from auth_workflows import (
    AUTH_REPOSITORY_EXTENSION,
    AuthAccount,
    AuthPersistenceError,
    DuplicateEmailError,
    install_auth_repository,
)
from isolation_rules import can_access_resource
from permissions import (
    INVENTORY_VIEW,
    ROLE_PERMISSIONS,
    USERS_MANAGE,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from security import hash_password, verify_password
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_AUTH_EXTENSION,
    SESSION_COOKIE_NAME,
    SessionManager,
    TrustedSessionUser,
    install_session_auth,
    require_authenticated_session,
)
from rate_limiting import (
    LOGIN_RATE_LIMIT_EXTENSION,
    RateLimitDecision,
    install_login_rate_limiter,
)


class MemorySessionStore:
    """Test-only store for route tests; no production fallback uses it."""

    def __init__(self):
        self.records = {}
        self.fail_put = False
        self.fail_delete = False

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        if self.fail_put:
            raise RuntimeError("private session store failure")
        self.records[token_digest] = record

    def delete(self, token_digest):
        if self.fail_delete:
            raise RuntimeError("private session revocation failure")
        self.records.pop(token_digest, None)


class FakeRateLimitStore:
    """Test-only atomic counter substitute for route orchestration tests."""

    def __init__(self, *, allowed=True, fail=False):
        self.allowed = allowed
        self.fail = fail
        self.calls = []

    def consume(self, key, *, limit, window_seconds):
        self.calls.append((key, limit, window_seconds))
        if self.fail:
            raise RuntimeError("private rate limit store failure")
        return RateLimitDecision(
            allowed=self.allowed,
            remaining=0 if not self.allowed else limit - 1,
            retry_after_seconds=60 if not self.allowed else 0,
        )


class FakeAuthRepository:
    def __init__(self, account=None):
        self.account = account
        self.registrations = []
        self.fail_lookup = False
        self.fail_registration = False
        self.lookups = 0

    def find_user_by_email(self, email):
        self.lookups += 1
        if self.fail_lookup:
            raise AuthPersistenceError("private lookup error")
        if self.account and email.casefold() == "owner@example.com":
            return self.account
        return None

    def register_owner_and_business_atomic(
        self, *, owner_name, business_name, email, password_hash
    ):
        if self.fail_registration:
            raise AuthPersistenceError("private registration error")
        if email.casefold() == "owner@example.com" and self.account:
            raise DuplicateEmailError("duplicate")
        self.registrations.append(
            {
                "owner_name": owner_name,
                "business_name": business_name,
                "email": email,
                "password_hash": password_hash,
            }
        )


class FakeResourceRepository:
    """Test-only trusted resource scopes; never installed by production app."""

    def __init__(self, scopes):
        self.scopes = dict(scopes)

    def get_scope(self, resource_id):
        return self.scopes.get(resource_id)


def make_account(
    *, active=True, deleted=False, branch_count=0, access_version=3, principal=None
):
    return AuthAccount(
        user_id=17,
        password_hash=hash_password("correct-password"),
        access_version=access_version,
        is_active=active,
        is_deleted=deleted,
        principal=principal
        or AuthenticatedPrincipal(
            role=Role.OWNER,
            business_id=8,
            branch_id=None,
            permissions=ROLE_PERMISSIONS[Role.OWNER],
        ),
        business_branch_count=branch_count,
    )


def make_auth_client(*, account=None, store=None, secure_cookie=False):
    app = create_app(
        {"TESTING": True, "SESSION_COOKIE_SECURE": secure_cookie}
    )
    repository = FakeAuthRepository(account)
    store = store or MemorySessionStore()
    install_auth_repository(app, repository)
    rate_limit_store = FakeRateLimitStore()
    install_login_rate_limiter(app, rate_limit_store, b"k" * 32)

    def load_user(user_id):
        current_account = repository.account
        if current_account is None or current_account.user_id != user_id:
            return None
        return TrustedSessionUser(
            user_id=current_account.user_id,
            access_version=current_account.access_version,
            is_active=current_account.is_active,
            is_deleted=current_account.is_deleted,
            principal=current_account.principal,
        )

    manager = install_session_auth(
        app,
        store,
        load_user,
        clock=lambda: datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    return app, app.test_client(), repository, store, manager


def install_authorization_test_routes(app, resource_repository):
    """Compose session loading and authorization in test-only Flask routes."""
    app.extensions["test.resource_repository"] = resource_repository

    @app.get("/api/testing/admin")
    @require_authenticated_session
    def test_admin_route():
        principal = g.authenticated_session.principal
        scope = ResourceScope(business_id=8, branch_id=12)
        if not can_access_resource(principal, USERS_MANAGE, scope):
            return jsonify({"error": "Resource not found or access denied."}), 404
        return jsonify({"allowed": True}), 200

    @app.get("/api/testing/resources/<int:resource_id>")
    @require_authenticated_session
    def test_resource_route(resource_id):
        repository = current_app.extensions["test.resource_repository"]
        resource_scope = repository.get_scope(resource_id)
        if resource_scope is None or not can_access_resource(
            g.authenticated_session.principal,
            INVENTORY_VIEW,
            resource_scope,
        ):
            return jsonify({"error": "Resource not found or access denied."}), 404
        return jsonify({"resource_id": resource_id, "visible": True}), 200


def make_authorization_client(account, resource_scopes):
    app, client, repository, store, manager = make_auth_client(account=account)
    install_authorization_test_routes(
        app, FakeResourceRepository(resource_scopes)
    )
    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    return app, client, repository, store, manager


def test_registration_succeeds_without_creating_a_branch():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository()
    install_auth_repository(app, repository)

    response = app.test_client().post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": " Owner@Example.com ",
            "password": "correct-password",
        },
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "data": {
            "owner_created": True,
            "business_created": True,
            "branch_created": False,
        },
        "message": "Registration successful.",
    }
    saved = repository.registrations[0]
    assert saved["email"] == "owner@example.com"
    assert verify_password("correct-password", saved["password_hash"])
    assert "correct-password" not in saved["password_hash"]
    assert SESSION_AUTH_EXTENSION not in app.extensions


def test_registration_duplicate_email_is_case_insensitive_and_conflicts():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository(make_account())
    install_auth_repository(app, repository)

    response = app.test_client().post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": "OWNER@example.com",
            "password": "correct-password",
        },
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "An account with this email already exists."
    }
    assert repository.registrations == []


def test_registration_persistence_error_returns_generic_server_error():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository()
    repository.fail_lookup = True
    install_auth_repository(app, repository)

    response = app.test_client().post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": "owner@example.com",
            "password": "correct-password",
        },
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Registration could not be completed."}
    assert "private" not in response.get_data(as_text=True)


def test_registration_atomic_write_failure_returns_generic_server_error():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository()
    repository.fail_registration = True
    install_auth_repository(app, repository)

    response = app.test_client().post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": "owner@example.com",
            "password": "correct-password",
        },
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Registration could not be completed."}
    assert repository.registrations == []
    assert "private" not in response.get_data(as_text=True)


def test_login_with_missing_session_persistence_preserves_501_behavior():
    app = create_app({"TESTING": True})
    install_auth_repository(app, FakeAuthRepository(make_account()))
    install_login_rate_limiter(app, FakeRateLimitStore(), b"k" * 32)

    response = app.test_client().post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 501
    assert response.get_json() == {
        "message": "Database authentication is not implemented yet."
    }
    assert "Set-Cookie" not in response.headers


def test_login_without_rate_limit_store_remains_unimplemented():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository(make_account())
    install_auth_repository(app, repository)
    install_session_auth(app, MemorySessionStore(), lambda _user_id: None)

    response = app.test_client().post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 501
    assert repository.lookups == 0


def test_login_succeeds_sets_secure_session_cookies_and_reports_first_branch():
    _app, client, _repository, store, _manager = make_auth_client(
        account=make_account(branch_count=0)
    )

    response = client.post(
        "/api/auth/login",
        json={"email": "OWNER@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "data": {
            "onboarding": {
                "required": True,
                "message": "Create the first branch for your business to complete onboarding.",
            }
        },
        "message": "Login successful.",
    }
    session_cookie = client.get_cookie(SESSION_COOKIE_NAME, path="/api")
    csrf_cookie = client.get_cookie(CSRF_COOKIE_NAME, path="/api")
    assert session_cookie is not None and session_cookie.http_only
    assert csrf_cookie is not None and not csrf_cookie.http_only
    assert session_cookie.same_site == "Lax"
    assert len(store.records) == 1


def test_login_honors_secure_cookie_setting_for_both_auth_cookies():
    _app, client, _repository, _store, _manager = make_auth_client(
        account=make_account(branch_count=1), secure_cookie=True
    )

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    session_cookie = client.get_cookie(SESSION_COOKIE_NAME, path="/api")
    csrf_cookie = client.get_cookie(CSRF_COOKIE_NAME, path="/api")
    assert session_cookie is not None and session_cookie.secure
    assert csrf_cookie is not None and csrf_cookie.secure
    assert session_cookie.http_only
    assert not csrf_cookie.http_only


def test_login_saves_access_version_at_login_and_rechecks_current_version():
    _app, client, repository, store, _manager = make_auth_client(
        account=make_account(access_version=7)
    )

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert len(store.records) == 1
    session_record = next(iter(store.records.values()))
    assert session_record.access_version_at_login == 7

    # The approved users.access_version is reloaded on each authenticated request.
    repository.account = replace(repository.account, access_version=8)
    response = client.post(
        "/api/auth/logout",
        headers={
            CSRF_HEADER_NAME: client.get_cookie(CSRF_COOKIE_NAME, path="/api").value
        },
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication is required."}
    assert store.records == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "unknown@example.com", "password": "correct-password"},
        {"email": "owner@example.com", "password": "wrong-password"},
    ],
)
def test_login_invalid_credentials_returns_401_without_session(payload):
    _app, client, _repository, store, _manager = make_auth_client(
        account=make_account()
    )

    response = client.post("/api/auth/login", json=payload)

    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid email or password."}
    assert store.records == {}
    assert client.get_cookie(SESSION_COOKIE_NAME) is None


def test_login_blocks_inactive_account():
    _app, client, _repository, store, _manager = make_auth_client(
        account=make_account(active=False)
    )

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 401
    assert store.records == {}


def test_login_session_store_failure_returns_500_without_cookies():
    store = MemorySessionStore()
    store.fail_put = True
    _app, client, _repository, _store, _manager = make_auth_client(
        account=make_account(), store=store
    )

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "An unexpected server error occurred."}
    assert "Set-Cookie" not in response.headers
    assert store.records == {}


def test_login_repository_failure_returns_generic_service_error_without_cookie():
    _app, client, repository, store, _manager = make_auth_client(
        account=make_account()
    )
    repository.fail_lookup = True

    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Authentication service is unavailable."}
    assert "Set-Cookie" not in response.headers
    assert store.records == {}
    assert "private" not in response.get_data(as_text=True)


def test_login_rate_limit_denial_returns_429_without_authenticating():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository(make_account())
    install_auth_repository(app, repository)
    store = MemorySessionStore()
    install_session_auth(
        app,
        store,
        lambda _user_id: None,
        clock=lambda: datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    limiter_store = FakeRateLimitStore(allowed=False)
    install_login_rate_limiter(app, limiter_store, b"k" * 32)

    response = app.test_client().post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.get_json() == {
        "error": "Too many login attempts. Try again later."
    }
    assert store.records == {}
    # The address bucket is checked first and short-circuits identifier work.
    assert len(limiter_store.calls) == 1


def test_login_rate_limit_store_failure_fails_closed():
    app = create_app({"TESTING": True})
    repository = FakeAuthRepository(make_account())
    repository.fail_lookup = True
    install_auth_repository(app, repository)
    store = MemorySessionStore()
    install_session_auth(app, store, lambda _user_id: None)
    install_login_rate_limiter(
        app, FakeRateLimitStore(fail=True), b"k" * 32
    )

    response = app.test_client().post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Authentication service is unavailable."}
    assert store.records == {}
    assert repository.lookups == 0


def test_logout_rejects_unauthorized_access_when_session_adapter_is_installed():
    _app, client, _repository, _store, _manager = make_auth_client(
        account=make_account()
    )

    response = client.post("/api/auth/logout")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication is required."}


def test_logout_requires_csrf_before_revoking_a_session():
    _app, client, _repository, store, _manager = make_auth_client(
        account=make_account()
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    assert login.status_code == 200

    response = client.post("/api/auth/logout")

    assert response.status_code == 403
    assert response.get_json() == {"error": "CSRF validation failed."}
    assert len(store.records) == 1


def test_logout_rejects_mismatched_csrf_cookie_and_header_without_revoking():
    _app, client, _repository, store, _manager = make_auth_client(
        account=make_account()
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    assert login.status_code == 200
    csrf_cookie = client.get_cookie(CSRF_COOKIE_NAME, path="/api").value

    response = client.post(
        "/api/auth/logout",
        headers={CSRF_HEADER_NAME: "Z" * 43},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "CSRF validation failed."}
    assert client.get_cookie(CSRF_COOKIE_NAME, path="/api").value == csrf_cookie
    assert len(store.records) == 1


def test_logout_revokes_session_and_clears_both_cookies():
    _app, client, _repository, store, manager = make_auth_client(
        account=make_account()
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    assert login.status_code == 200
    session_token = client.get_cookie(SESSION_COOKIE_NAME, path="/api").value
    csrf_token = client.get_cookie(CSRF_COOKIE_NAME, path="/api").value

    response = client.post(
        "/api/auth/logout", headers={CSRF_HEADER_NAME: csrf_token}
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Logout successful."}
    assert client.get_cookie(SESSION_COOKIE_NAME, path="/api") is None
    assert client.get_cookie(CSRF_COOKIE_NAME, path="/api") is None
    assert store.records == {}
    assert manager.resolve_session(session_token) is None


def test_logout_persistence_failure_does_not_claim_success_or_clear_cookies():
    store = MemorySessionStore()
    _app, client, _repository, _store, _manager = make_auth_client(
        account=make_account(), store=store
    )
    client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    csrf_token = client.get_cookie(CSRF_COOKIE_NAME, path="/api").value
    store.fail_delete = True

    response = client.post(
        "/api/auth/logout", headers={CSRF_HEADER_NAME: csrf_token}
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Logout could not be completed."}
    assert client.get_cookie(SESSION_COOKIE_NAME, path="/api") is not None
    assert len(store.records) == 1


def test_production_rejects_repositories_and_session_stores_without_persistence_marker():
    app = create_app(
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "k" * 32,
            "SQLALCHEMY_DATABASE_URI": "mysql+mysqlconnector://test:test@db.test/branchwise",
        }
    )
    with pytest.raises(ValueError, match="persistent storage"):
        install_auth_repository(app, FakeAuthRepository())
    with pytest.raises(ValueError, match="persistent storage"):
        install_session_auth(app, MemorySessionStore(), lambda _user_id: None)
    with pytest.raises(ValueError, match="persistent storage"):
        install_login_rate_limiter(app, FakeRateLimitStore(), b"k" * 32)


def test_no_auth_repository_is_installed_by_default():
    app = create_app()

    assert AUTH_REPOSITORY_EXTENSION not in app.extensions
    assert SESSION_AUTH_EXTENSION not in app.extensions
    assert LOGIN_RATE_LIMIT_EXTENSION not in app.extensions


def test_production_without_persistent_auth_adapters_keeps_auth_routes_at_501():
    app = create_app(
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "k" * 32,
            "SQLALCHEMY_DATABASE_URI": "mysql+mysqlconnector://test:test@db.test/branchwise",
            "TESTING": True,
        }
    )
    client = app.test_client()

    registration = client.post(
        "/api/auth/register",
        json={
            "owner_name": "Owner",
            "business_name": "Business",
            "email": "owner@example.com",
            "password": "correct-password",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    logout = client.post("/api/auth/logout")

    assert registration.status_code == 501
    assert login.status_code == 501
    assert logout.status_code == 501
    assert AUTH_REPOSITORY_EXTENSION not in app.extensions
    assert SESSION_AUTH_EXTENSION not in app.extensions
    assert LOGIN_RATE_LIMIT_EXTENSION not in app.extensions


def test_flask_client_enforces_role_permissions_using_loaded_session_principal():
    employee = AuthenticatedPrincipal(
        role=Role.EMPLOYEE,
        business_id=8,
        branch_id=12,
        permissions=ROLE_PERMISSIONS[Role.EMPLOYEE],
    )
    _app, client, _repository, _store, _manager = make_authorization_client(
        make_account(principal=employee),
        {101: ResourceScope(business_id=8, branch_id=12)},
    )

    response = client.get("/api/testing/admin")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "Resource not found or access denied."
    }


def test_flask_client_enforces_business_isolation_from_repository_scopes():
    owner = AuthenticatedPrincipal(
        role=Role.OWNER,
        business_id=8,
        branch_id=None,
        permissions=ROLE_PERMISSIONS[Role.OWNER],
    )
    _app, client, _repository, _store, _manager = make_authorization_client(
        make_account(principal=owner),
        {
            101: ResourceScope(business_id=8, branch_id=22),
            202: ResourceScope(business_id=99, branch_id=22),
        },
    )

    own_business = client.get("/api/testing/resources/101")
    forged_scope = client.get(
        "/api/testing/resources/202?business_id=8&branch_id=22"
    )

    assert own_business.status_code == 200
    assert forged_scope.status_code == 404
    assert forged_scope.get_json() == {
        "error": "Resource not found or access denied."
    }


def test_flask_client_enforces_branch_manager_assignment_and_business_scope():
    branch_manager = AuthenticatedPrincipal(
        role=Role.BRANCH_MANAGER,
        business_id=8,
        branch_id=12,
        permissions=ROLE_PERMISSIONS[Role.BRANCH_MANAGER],
    )
    _app, client, _repository, _store, _manager = make_authorization_client(
        make_account(principal=branch_manager),
        {
            101: ResourceScope(business_id=8, branch_id=12),
            102: ResourceScope(business_id=8, branch_id=13),
            202: ResourceScope(business_id=99, branch_id=12),
        },
    )

    assigned_branch = client.get("/api/testing/resources/101")
    other_branch = client.get("/api/testing/resources/102")
    other_business = client.get("/api/testing/resources/202")

    assert assigned_branch.status_code == 200
    assert other_branch.status_code == 404
    assert other_business.status_code == 404
