"""Unit tests for session foundations; stores and identities stay test-local."""

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g, jsonify

from permissions import ROLE_PERMISSIONS, AuthenticatedPrincipal, Role
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    DEFAULT_SESSION_TTL_SECONDS,
    MAX_SESSION_TTL_SECONDS,
    SESSION_AUTH_EXTENSION,
    SESSION_COOKIE_NAME,
    SESSION_TOKEN_BYTES,
    SessionManager,
    SessionRecord,
    TrustedSessionUser,
    clear_session_cookies,
    require_authenticated_session,
    set_session_cookies,
)


class MemorySessionStore:
    """A test-only store; production code has no in-memory implementation."""

    def __init__(self):
        self.records = {}
        self.deleted = []

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        self.records[token_digest] = record

    def delete(self, token_digest):
        self.deleted.append(token_digest)
        self.records.pop(token_digest, None)


class MutableClock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 11, 6, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


def make_principal(
    role=Role.OWNER,
    *,
    business_id=10,
    branch_id=None,
    permissions=None,
):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=(
            ROLE_PERMISSIONS.get(role, frozenset())
            if permissions is None
            else permissions
        ),
    )


def make_user(
    *,
    user_id=7,
    access_version=1,
    is_active=True,
    is_deleted=False,
    principal=None,
):
    return TrustedSessionUser(
        user_id=user_id,
        access_version=access_version,
        is_active=is_active,
        is_deleted=is_deleted,
        principal=principal or make_principal(),
    )


@pytest.fixture
def auth_components():
    store = MemorySessionStore()
    user = make_user()
    loaded = []

    def load_user(user_id):
        loaded.append(user_id)
        if user is not None and user.user_id == user_id:
            return user
        return None

    clock = MutableClock()
    manager = SessionManager(store, load_user, clock=clock)
    return store, user, loaded, clock, manager


def test_session_tokens_are_cryptographically_random_and_never_stored_raw(
    auth_components,
):
    store, user, _loaded, _clock, manager = auth_components

    first = manager.create_session(user)
    second = manager.create_session(user)

    assert len(first.session_token) == 43
    assert len(first.csrf_token) == 43
    assert first.session_token != second.session_token
    assert first.csrf_token != second.csrf_token
    assert first.session_token not in store.records
    assert all(first.session_token not in repr(record) for record in store.records.values())
    assert first.csrf_token not in repr(next(iter(store.records.values())))
    assert len(next(iter(store.records))) == 64


def test_session_tokens_request_32_bytes_of_entropy(monkeypatch, auth_components):
    import session_auth

    requested_sizes = []
    tokens = iter(("A" * 43, "B" * 43))

    def token_urlsafe(nbytes):
        requested_sizes.append(nbytes)
        return next(tokens)

    monkeypatch.setattr(session_auth.secrets, "token_urlsafe", token_urlsafe)
    store, user, _loaded, _clock, manager = auth_components

    issued = manager.create_session(user)

    assert requested_sizes == [SESSION_TOKEN_BYTES, SESSION_TOKEN_BYTES]
    assert SESSION_TOKEN_BYTES == 32
    assert issued.session_token == "A" * 43
    assert issued.csrf_token == "B" * 43
    assert len(store.records) == 1


def test_record_contains_only_minimal_non_authorization_session_data(
    auth_components,
):
    store, user, _loaded, _clock, manager = auth_components

    manager.create_session(user)
    record = next(iter(store.records.values()))

    assert type(record) is SessionRecord
    assert {field.name for field in fields(record)} == {
        "user_id",
        "access_version_at_login",
        "expires_at",
        "csrf_token_digest",
    }
    assert not hasattr(record, "password_hash")
    assert not hasattr(record, "role")
    assert not hasattr(record, "permissions")
    assert not hasattr(record, "business_id")
    assert not hasattr(record, "branch_id")


@pytest.mark.parametrize(
    "user",
    [
        None,
        make_user(is_active=False),
        make_user(is_deleted=True),
        make_user(user_id=True),
        make_user(access_version=True),
        make_user(principal=make_principal("Owner")),
        make_user(principal=make_principal(business_id=True)),
    ],
)
def test_session_creation_rejects_untrusted_or_disabled_accounts(user):
    store = MemorySessionStore()
    manager = SessionManager(store, lambda _user_id: user)

    with pytest.raises(ValueError, match="active trusted user"):
        manager.create_session(user)

    assert store.records == {}


@pytest.mark.parametrize("token", [None, "", "short", "a" * 42, "a" * 44, "!" * 43])
def test_invalid_token_shapes_are_rejected_without_user_lookup(token, auth_components):
    _store, _user, loaded, _clock, manager = auth_components

    assert manager.resolve_session(token) is None
    assert not manager.revoke_session(token)
    assert loaded == []


def test_well_shaped_but_unknown_and_modified_tokens_are_rejected(auth_components):
    _store, user, _loaded, _clock, manager = auth_components
    issued = manager.create_session(user)
    changed = ("A" if issued.session_token[0] != "A" else "B") + issued.session_token[1:]

    assert manager.resolve_session("Z" * 43) is None
    assert manager.resolve_session(changed) is None


def test_session_resolution_loads_fresh_server_identity(auth_components):
    _store, user, loaded, _clock, manager = auth_components
    issued = manager.create_session(user)

    context = manager.resolve_session(issued.session_token)

    assert context.user_id == user.user_id
    assert context.principal is user.principal
    assert loaded == [user.user_id]
    assert not hasattr(context, "password_hash")


def test_user_loader_reflects_current_role_and_branch_assignments(auth_components):
    store, _user, _loaded, clock, _manager = auth_components
    current_user = {"value": make_user()}
    manager = SessionManager(store, lambda _user_id: current_user["value"], clock=clock)
    issued = manager.create_session(current_user["value"])
    updated_principal = make_principal(
        Role.BRANCH_MANAGER,
        business_id=10,
        branch_id=22,
    )
    current_user["value"] = make_user(
        principal=updated_principal,
    )

    context = manager.resolve_session(issued.session_token)

    assert context.principal is updated_principal
    assert context.principal.role is Role.BRANCH_MANAGER
    assert context.principal.branch_id == 22


@pytest.mark.parametrize(
    "disabled_user",
    [make_user(is_active=False), make_user(is_deleted=True), None],
)
def test_disabled_deleted_or_missing_users_lose_existing_sessions(disabled_user):
    store = MemorySessionStore()
    current = {"value": make_user()}
    manager = SessionManager(store, lambda _user_id: current["value"])
    issued = manager.create_session(current["value"])
    current["value"] = disabled_user
    digest = next(iter(store.records))

    assert manager.resolve_session(issued.session_token) is None
    assert digest not in store.records
    assert digest in store.deleted


def test_user_id_mismatch_from_user_loader_revokes_session(auth_components):
    store, _user, _loaded, clock, _manager = auth_components
    manager = SessionManager(store, lambda _user_id: make_user(user_id=8), clock=clock)
    issued = manager.create_session(make_user())
    digest = next(iter(store.records))

    assert manager.resolve_session(issued.session_token) is None
    assert digest not in store.records


def test_access_version_change_invalidates_sessions_after_account_change(
    auth_components,
):
    store, _user, _loaded, clock, _manager = auth_components
    current_user = {"value": make_user(access_version=3)}
    manager = SessionManager(store, lambda _user_id: current_user["value"], clock=clock)
    issued = manager.create_session(current_user["value"])
    current_user["value"] = make_user(access_version=4)
    digest = next(iter(store.records))

    assert manager.resolve_session(issued.session_token) is None
    assert digest not in store.records


def test_expired_session_is_deleted_without_loading_the_user(auth_components):
    store, user, loaded, clock, manager = auth_components
    issued = manager.create_session(user)
    digest = next(iter(store.records))
    clock.value = issued.expires_at

    assert manager.resolve_session(issued.session_token) is None
    assert digest not in store.records
    assert digest in store.deleted
    assert loaded == []


def test_session_has_fixed_twelve_hour_lifetime_without_sliding_extension(
    auth_components,
):
    _store, user, _loaded, clock, manager = auth_components
    issued = manager.create_session(user)

    assert issued.max_age_seconds == DEFAULT_SESSION_TTL_SECONDS
    assert issued.expires_at - clock.value == timedelta(hours=12)
    clock.value += timedelta(minutes=30)
    context = manager.resolve_session(issued.session_token)
    assert context.expires_at == issued.expires_at


def test_revocation_deletes_session_and_is_idempotent(auth_components):
    store, user, _loaded, _clock, manager = auth_components
    issued = manager.create_session(user)
    digest = next(iter(store.records))

    assert manager.revoke_session(issued.session_token)
    assert digest not in store.records
    assert manager.resolve_session(issued.session_token) is None
    assert manager.revoke_session(issued.session_token)


@pytest.mark.parametrize("ttl", [True, 0, -1, 1.5, MAX_SESSION_TTL_SECONDS + 1])
def test_session_lifetime_must_be_a_bounded_positive_integer(ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        SessionManager(MemorySessionStore(), lambda _user_id: None, ttl_seconds=ttl)


def test_cookie_flags_are_secure_in_production_and_keep_auth_token_http_only(
    auth_components,
):
    _store, user, _loaded, _clock, manager = auth_components
    issued = manager.create_session(user)
    app = Flask(__name__)
    with app.app_context():
        response = set_session_cookies(jsonify({"ok": True}), issued, production=True)

    set_cookies = response.headers.getlist("Set-Cookie")
    session_cookie = next(value for value in set_cookies if value.startswith(SESSION_COOKIE_NAME))
    csrf_cookie = next(value for value in set_cookies if value.startswith(CSRF_COOKIE_NAME))
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie and "SameSite=Lax" in session_cookie
    assert "Path=/api" in session_cookie and "Max-Age=43200" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie and "SameSite=Lax" in csrf_cookie
    assert "Path=/api" in csrf_cookie and "Max-Age=43200" in csrf_cookie
    assert issued.session_token in session_cookie
    assert issued.csrf_token in csrf_cookie
    assert "role" not in session_cookie.lower()
    assert "business_id" not in session_cookie.lower()


def test_cookie_flags_allow_local_http_only_when_not_in_production(auth_components):
    _store, user, _loaded, _clock, manager = auth_components
    issued = manager.create_session(user)
    app = Flask(__name__)
    with app.app_context():
        response = set_session_cookies(jsonify({"ok": True}), issued, production=False)

    session_cookie = next(
        value
        for value in response.headers.getlist("Set-Cookie")
        if value.startswith(SESSION_COOKIE_NAME)
    )
    assert "HttpOnly" in session_cookie
    assert "Secure" not in session_cookie
    assert "SameSite=Lax" in session_cookie


def test_cookie_clearing_uses_matching_scope_and_secure_settings():
    app = Flask(__name__)
    with app.app_context():
        response = clear_session_cookies(jsonify({"ok": True}), production=True)

    headers = response.headers.getlist("Set-Cookie")
    assert len(headers) == 2
    assert all("Max-Age=0" in value and "Secure" in value for value in headers)
    assert all("Path=/api" in value and "SameSite=Lax" in value for value in headers)


def test_csrf_requires_matching_session_bound_cookie_and_header(auth_components):
    _store, user, _loaded, _clock, manager = auth_components
    issued = manager.create_session(user)
    context = manager.resolve_session(issued.session_token)

    assert manager.csrf_token_is_valid(context, issued.csrf_token, issued.csrf_token)
    assert not manager.csrf_token_is_valid(context, issued.csrf_token, "Z" * 43)
    other = manager.create_session(user)
    assert not manager.csrf_token_is_valid(context, other.csrf_token, other.csrf_token)
    assert not manager.csrf_token_is_valid(context, None, issued.csrf_token)


def make_protected_app(manager=None):
    app = Flask(__name__)
    if manager is not None:
        app.extensions[SESSION_AUTH_EXTENSION] = manager

    @app.get("/api/protected")
    @require_authenticated_session
    def protected():
        return jsonify({"user_id": g.authenticated_session.user_id})

    @app.post("/api/protected")
    @require_authenticated_session
    def protected_mutation():
        return jsonify(
            {
                "user_id": g.authenticated_session.user_id,
                "role": g.authenticated_session.principal.role.value,
                "business_id": g.authenticated_session.principal.business_id,
                "branch_id": g.authenticated_session.principal.branch_id,
            }
        )

    return app


def test_protected_route_requires_explicitly_installed_session_manager():
    client = make_protected_app().test_client()

    response = client.get("/api/protected")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication is required."}


def test_production_rejects_worker_local_persistent_session_store():
    from app import create_app
    from session_auth import install_session_auth

    app = create_app(
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "k" * 32,
            "SQLALCHEMY_DATABASE_URI": "mysql+mysqlconnector://test:test@db.test/branchwise",
        }
    )
    store = MemorySessionStore()
    store.is_persistent = True

    with pytest.raises(ValueError, match="shared across workers"):
        install_session_auth(app, store, lambda _user_id: make_user())


def test_protected_route_uses_cookie_session_and_ignores_client_identity_fields(
    auth_components,
):
    store, user, _loaded, clock, _manager = auth_components
    manager = SessionManager(store, lambda _user_id: user, clock=clock)
    issued = manager.create_session(user)
    app = make_protected_app(manager)
    client = app.test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")
    client.set_cookie(CSRF_COOKIE_NAME, issued.csrf_token, path="/api")

    response = client.post(
        "/api/protected?user_id=99&role=Owner&business_id=999&branch_id=888",
        json={
            "user_id": 99,
            "role": "Owner",
            "business_id": 999,
            "branch_id": 888,
        },
        headers={CSRF_HEADER_NAME: issued.csrf_token},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "user_id": user.user_id,
        "role": "Owner",
        "business_id": 10,
        "branch_id": None,
    }


def test_protected_state_change_requires_csrf_cookie_and_header(auth_components):
    store, user, _loaded, clock, _manager = auth_components
    manager = SessionManager(store, lambda _user_id: user, clock=clock)
    issued = manager.create_session(user)
    client = make_protected_app(manager).test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")

    response = client.post("/api/protected")

    assert response.status_code == 403
    assert response.get_json() == {"error": "CSRF validation failed."}


def test_valid_session_allows_safe_route_without_csrf(auth_components):
    store, user, _loaded, clock, _manager = auth_components
    manager = SessionManager(store, lambda _user_id: user, clock=clock)
    issued = manager.create_session(user)
    client = make_protected_app(manager).test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")

    response = client.get("/api/protected")

    assert response.status_code == 200
    assert response.get_json() == {"user_id": user.user_id}
