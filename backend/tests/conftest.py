"""Shared trusted-session fixtures for protected API tests."""

from datetime import datetime, timezone

import pytest

from app import create_app
from permissions import ROLE_PERMISSIONS, AuthenticatedPrincipal, Role
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    TrustedSessionUser,
    install_session_auth,
)


class TestSessionStore:
    def __init__(self):
        self.records = {}

    def get(self, digest):
        return self.records.get(digest)

    def put(self, digest, record):
        self.records[digest] = record

    def delete(self, digest):
        self.records.pop(digest, None)


@pytest.fixture
def owner_client():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    return app, make_owner_client(app)


def make_owner_client(app):
    return make_role_client(app, role=Role.OWNER)


def make_role_client(app, *, role, business_id=20, branch_id=30):
    user = TrustedSessionUser(
        user_id=10,
        access_version=1,
        is_active=True,
        is_deleted=False,
        principal=AuthenticatedPrincipal(
            role=role,
            business_id=business_id,
            branch_id=None if role is Role.OWNER else branch_id,
            permissions=ROLE_PERMISSIONS[role],
        ),
    )
    manager = install_session_auth(
        app,
        TestSessionStore(),
        lambda user_id: user if user_id == user.user_id else None,
        clock=lambda: datetime(2026, 11, 14, tzinfo=timezone.utc),
    )
    issued = manager.create_session(user)
    client = app.test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")
    client.set_cookie(CSRF_COOKIE_NAME, issued.csrf_token, path="/api")
    client.environ_base["HTTP_" + CSRF_HEADER_NAME.upper().replace("-", "_")] = issued.csrf_token
    return client
