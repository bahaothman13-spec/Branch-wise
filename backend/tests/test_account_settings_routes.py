"""Focused session, authorization, validation, and repository tests."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from account_settings_rules import AccountRecord, BusinessSettingsRecord
from app import create_app
from permissions import ROLE_PERMISSIONS, AuthenticatedPrincipal, Role
from repository_contracts import (
    BUSINESS_SETTINGS_REPOSITORY_EXTENSION,
    USER_REPOSITORY_EXTENSION,
    AccountProfileUpdateData,
    BusinessSettingsUpdateData,
    install_business_settings_repository,
    install_user_repository,
)
from security import hash_password, verify_password
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    TrustedSessionUser,
    install_session_auth,
)


USER_ID = 23
BUSINESS_ID = 10


class MemorySessionStore:
    """Test-only session store; no application factory installs this adapter."""

    def __init__(self):
        self.records = {}

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        self.records[token_digest] = record

    def delete(self, token_digest):
        self.records.pop(token_digest, None)


class FakeUserRepository:
    """Test-only account adapter used to exercise route orchestration."""

    def __init__(self, account):
        self.account = account
        self.profile_updates = []
        self.password_hashes = []

    def list_users(self, **_kwargs):
        raise AssertionError("User-management APIs are outside this test.")

    def get_own_account(self, *, actor):
        assert actor.user_id == self.account.user_id
        return self.account

    def update_own_profile_atomic(self, *, actor, data: AccountProfileUpdateData):
        assert actor.user_id == self.account.user_id
        self.profile_updates.append(data)
        self.account = replace(self.account, **dict(data.changes))
        return self.account

    def create_user_atomic(self, **_kwargs):
        raise AssertionError("User-management APIs are outside this test.")

    def set_user_active_atomic(self, **_kwargs):
        raise AssertionError("User-management APIs are outside this test.")

    def assign_user_atomic(self, **_kwargs):
        raise AssertionError("User-management APIs are outside this test.")

    def change_own_password_atomic(self, *, actor, new_password_hash):
        assert actor.user_id == self.account.user_id
        self.password_hashes.append(new_password_hash)


class FakeBusinessSettingsRepository:
    """Test-only business settings adapter used to exercise route orchestration."""

    def __init__(self, business):
        self.business = business
        self.updates = []

    def get_own_business_settings(self, *, actor):
        assert actor.principal.business_id == self.business.business_id
        return self.business

    def update_own_business_settings_atomic(
        self, *, actor, data: BusinessSettingsUpdateData
    ):
        assert actor.principal.business_id == self.business.business_id
        self.updates.append(data)
        self.business = replace(self.business, **dict(data.changes))
        return self.business


def _principal(role=Role.OWNER, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=BUSINESS_ID,
        branch_id=None if role is Role.OWNER else 22,
        permissions=(
            ROLE_PERMISSIONS[role]
            if permissions is None
            else frozenset(permissions)
        ),
    )


def _account(role=Role.OWNER):
    return AccountRecord(
        user_id=USER_ID,
        business_id=BUSINESS_ID,
        name="Test Owner" if role is Role.OWNER else "Branch Manager",
        email="owner@example.com" if role is Role.OWNER else "manager@example.com",
        role=role,
        branch_id=None if role is Role.OWNER else 22,
        is_active=True,
        password_hash=hash_password("current-password"),
    )


def make_client(
    *, role=Role.OWNER, permissions=None, with_repositories=True
):
    app = create_app({"APP_ENV": "test", "TESTING": True})
    principal = _principal(role, permissions)
    user = TrustedSessionUser(
        user_id=USER_ID,
        access_version=3,
        is_active=True,
        is_deleted=False,
        principal=principal,
    )
    manager = install_session_auth(
        app,
        MemorySessionStore(),
        lambda user_id: user if user_id == USER_ID else None,
        clock=lambda: datetime(2026, 11, 14, tzinfo=timezone.utc),
    )
    issued = manager.create_session(user)
    client = app.test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")
    client.set_cookie(CSRF_COOKIE_NAME, issued.csrf_token, path="/api")

    user_repository = FakeUserRepository(_account(role))
    business_repository = FakeBusinessSettingsRepository(
        BusinessSettingsRecord(
            business_id=BUSINESS_ID,
            business_name="BranchWise Market",
            email="owner@example.com",
        )
    )
    if with_repositories:
        install_user_repository(app, user_repository)
        install_business_settings_repository(app, business_repository)

    return (
        app,
        client,
        user_repository,
        business_repository,
        {CSRF_HEADER_NAME: issued.csrf_token},
    )


def test_account_and_business_settings_reads_and_updates_use_scoped_rules():
    _, client, user_repository, business_repository, csrf = make_client()

    account_response = client.get("/api/users/me")
    business_response = client.get("/api/business/settings")
    profile_response = client.patch(
        "/api/users/me",
        json={"name": "  Updated Owner  ", "email": " new@example.com "},
        headers=csrf,
    )
    business_update_response = client.patch(
        "/api/business/settings",
        json={"business_name": "  New Market  ", "email": " contact@example.com "},
        headers=csrf,
    )

    assert account_response.status_code == 200
    assert account_response.get_json()["data"] == {
        "user_id": USER_ID,
        "name": "Test Owner",
        "email": "owner@example.com",
        "role": "Owner",
        "business_id": BUSINESS_ID,
        "branch_id": None,
        "is_active": True,
    }
    assert "password_hash" not in account_response.get_data(as_text=True)
    assert business_response.status_code == 200
    assert business_response.get_json()["data"] == {
        "business_id": BUSINESS_ID,
        "business_name": "BranchWise Market",
        "email": "owner@example.com",
    }
    assert profile_response.status_code == 200
    assert profile_response.get_json()["data"]["name"] == "Updated Owner"
    assert profile_response.get_json()["data"]["email"] == "new@example.com"
    assert user_repository.profile_updates[0].changes == {
        "name": "Updated Owner",
        "email": "new@example.com",
    }
    assert business_update_response.status_code == 200
    assert business_update_response.get_json()["data"] == {
        "business_id": BUSINESS_ID,
        "business_name": "New Market",
        "email": "contact@example.com",
    }
    assert business_repository.updates[0].changes == {
        "business_name": "New Market",
        "email": "contact@example.com",
    }


def test_password_change_verifies_current_password_and_persists_only_a_hash():
    _, client, user_repository, _, csrf = make_client()

    response = client.patch(
        "/api/users/me/password",
        json={
            "current_password": "current-password",
            "new_password": "replacement-password",
            "confirm_password": "replacement-password",
        },
        headers=csrf,
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Password changed successfully."}
    saved_hash = user_repository.password_hashes[0]
    assert saved_hash != "replacement-password"
    assert verify_password("replacement-password", saved_hash)
    assert "replacement-password" not in repr(user_repository.password_hashes)


@pytest.mark.parametrize(
    ("path", "payload", "field"),
    [
        ("/api/users/me", {"email": "invalid-email"}, "email"),
        ("/api/users/me", {"role": "Owner"}, "body"),
        ("/api/business/settings", {"email": "invalid-email"}, "email"),
        ("/api/business/settings", {"phone": "+962 6 555 0100"}, "body"),
        (
            "/api/users/me/password",
            {
                "current_password": "current-password",
                "new_password": "replacement-password",
                "confirm_password": "different-password",
            },
            "confirm_password",
        ),
    ],
)
def test_invalid_account_or_business_settings_input_returns_field_errors(
    path, payload, field
):
    _, client, _, _, csrf = make_client()

    response = client.patch(path, json=payload, headers=csrf)

    assert response.status_code == 400
    body = response.get_json()
    assert field in body["errors"]
    assert "phone" not in response.get_data(as_text=True)


def test_wrong_current_password_is_denied_without_echoing_passwords():
    _, client, _, _, csrf = make_client()
    wrong_password = "not-the-current-password"

    response = client.patch(
        "/api/users/me/password",
        json={
            "current_password": wrong_password,
            "new_password": "replacement-password",
            "confirm_password": "replacement-password",
        },
        headers=csrf,
    )

    assert response.status_code == 403
    response_text = response.get_data(as_text=True)
    assert wrong_password not in response_text
    assert "replacement-password" not in response_text


def test_business_settings_are_owner_only_and_state_changes_require_csrf():
    _, manager_client, _, business_repository, csrf = make_client(
        role=Role.BRANCH_MANAGER
    )
    _, owner_client, user_repository, _, _ = make_client()

    view_response = manager_client.get("/api/business/settings")
    update_response = manager_client.patch(
        "/api/business/settings",
        json={"business_name": "Changed"},
        headers=csrf,
    )
    missing_csrf_response = owner_client.patch(
        "/api/users/me",
        json={"name": "Changed"},
    )

    assert view_response.status_code == 403
    assert update_response.status_code == 403
    assert business_repository.updates == []
    assert missing_csrf_response.status_code == 403
    assert user_repository.profile_updates == []


def test_unauthenticated_account_request_is_rejected():
    app = create_app({"APP_ENV": "test", "TESTING": True})

    response = app.test_client().get("/api/users/me")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication is required."}


def test_valid_account_and_business_requests_return_501_without_repositories():
    app, client, _, _, csrf = make_client(with_repositories=False)

    responses = [
        client.get("/api/users/me"),
        client.patch("/api/users/me", json={"name": "Updated"}, headers=csrf),
        client.patch(
            "/api/users/me/password",
            json={
                "current_password": "current-password",
                "new_password": "replacement-password",
                "confirm_password": "replacement-password",
            },
            headers=csrf,
        ),
        client.get("/api/business/settings"),
        client.patch(
            "/api/business/settings",
            json={"business_name": "Updated"},
            headers=csrf,
        ),
    ]

    assert USER_REPOSITORY_EXTENSION not in app.extensions
    assert BUSINESS_SETTINGS_REPOSITORY_EXTENSION not in app.extensions
    assert [response.status_code for response in responses] == [501] * 5
    assert all("not available" in response.get_json()["error"] for response in responses)
