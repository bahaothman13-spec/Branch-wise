"""Unit tests for authentication orchestration with test-local adapters."""

from datetime import datetime, timezone

import pytest

from auth_workflows import (
    AuthAccount,
    AuthPersistenceError,
    DuplicateEmailError,
    InvalidCredentialsError,
    authenticate_user,
    register_owner,
)
from permissions import ROLE_PERMISSIONS, AuthenticatedPrincipal, Role
from security import hash_password, verify_password
from session_auth import SessionManager, TrustedSessionUser


class MemorySessionStore:
    """Test-only persistence adapter; never configured by the application."""

    def __init__(self):
        self.records = {}

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        self.records[token_digest] = record

    def delete(self, token_digest):
        self.records.pop(token_digest, None)


class FakeAuthRepository:
    def __init__(self, accounts=None):
        self.accounts = {
            email.casefold(): account for email, account in (accounts or {}).items()
        }
        self.registrations = []
        self.lookup_error = None
        self.registration_error = None

    def find_user_by_email(self, email):
        if self.lookup_error:
            raise self.lookup_error
        return self.accounts.get(email.casefold())

    def register_owner_and_business_atomic(
        self, *, owner_name, business_name, email, password_hash
    ):
        if self.registration_error:
            raise self.registration_error
        if email.casefold() in self.accounts:
            raise DuplicateEmailError("duplicate")
        self.registrations.append(
            {
                "owner_name": owner_name,
                "business_name": business_name,
                "email": email,
                "password_hash": password_hash,
            }
        )


def make_account(
    *,
    password="correct-password",
    is_active=True,
    is_deleted=False,
    principal=None,
    business_branch_count=0,
    access_version=4,
):
    principal = principal or AuthenticatedPrincipal(
        role=Role.OWNER,
        business_id=31,
        branch_id=None,
        permissions=ROLE_PERMISSIONS[Role.OWNER],
    )
    return AuthAccount(
        user_id=12,
        password_hash=hash_password(password),
        access_version=access_version,
        is_active=is_active,
        is_deleted=is_deleted,
        principal=principal,
        business_branch_count=business_branch_count,
    )


def make_session_manager(store, account):
    def load_user(user_id):
        if user_id != account.user_id:
            return None
        return TrustedSessionUser(
            user_id=account.user_id,
            access_version=account.access_version,
            is_active=account.is_active,
            is_deleted=account.is_deleted,
            principal=account.principal,
        )

    return SessionManager(
        store,
        load_user,
        clock=lambda: datetime(2026, 11, 7, tzinfo=timezone.utc),
    )


def test_register_owner_hashes_password_and_uses_atomic_no_branch_repository():
    repository = FakeAuthRepository()

    register_owner(
        repository,
        owner_name=" Test Owner ",
        business_name=" Test Business ",
        email=" Owner@Example.com ",
        password="correct-password",
    )

    assert len(repository.registrations) == 1
    registration = repository.registrations[0]
    assert registration["owner_name"] == "Test Owner"
    assert registration["business_name"] == "Test Business"
    assert registration["email"] == "owner@example.com"
    assert verify_password("correct-password", registration["password_hash"])
    assert registration["password_hash"] != "correct-password"
    assert "branch_id" not in registration


def test_register_owner_rejects_case_insensitive_duplicate_before_writing():
    existing = make_account()
    repository = FakeAuthRepository({"Owner@Example.com": existing})

    with pytest.raises(DuplicateEmailError):
        register_owner(
            repository,
            owner_name="Another Owner",
            business_name="Another Business",
            email=" OWNER@example.com ",
            password="correct-password",
        )

    assert repository.registrations == []


def test_register_owner_translates_atomic_duplicate_race():
    repository = FakeAuthRepository()
    repository.registration_error = DuplicateEmailError("duplicate")

    with pytest.raises(DuplicateEmailError):
        register_owner(
            repository,
            owner_name="Owner",
            business_name="Business",
            email="owner@example.com",
            password="correct-password",
        )


def test_registration_persistence_errors_are_not_reported_as_success():
    repository = FakeAuthRepository()
    repository.lookup_error = AuthPersistenceError("storage unavailable")

    with pytest.raises(AuthPersistenceError):
        register_owner(
            repository,
            owner_name="Owner",
            business_name="Business",
            email="owner@example.com",
            password="correct-password",
        )

    assert repository.registrations == []


def test_login_issues_session_with_access_version_and_first_branch_status():
    account = make_account(business_branch_count=0, access_version=9)
    repository = FakeAuthRepository({"owner@example.com": account})
    store = MemorySessionStore()
    manager = make_session_manager(store, account)

    outcome = authenticate_user(
        repository,
        manager,
        email=" OWNER@Example.com ",
        password="correct-password",
    )

    assert outcome.onboarding.required is True
    assert outcome.onboarding.message
    assert len(store.records) == 1
    record = next(iter(store.records.values()))
    assert record.user_id == account.user_id
    assert record.access_version_at_login == 9
    assert outcome.issued_session.session_token not in store.records


@pytest.mark.parametrize(
    "account",
    [None, make_account(is_active=False), make_account(is_deleted=True)],
)
def test_login_rejects_missing_wrong_or_disabled_credentials(account):
    repository = FakeAuthRepository(
        {} if account is None else {"owner@example.com": account}
    )
    store = MemorySessionStore()
    manager = make_session_manager(store, account or make_account())

    with pytest.raises(InvalidCredentialsError):
        authenticate_user(
            repository,
            manager,
            email="owner@example.com",
            password="wrong-password",
        )

    assert store.records == {}


def test_login_rejects_untrusted_repository_principal():
    invalid_principal = AuthenticatedPrincipal(
        role="Owner", business_id=31, branch_id=None, permissions=frozenset()
    )
    account = make_account(principal=invalid_principal)
    repository = FakeAuthRepository({"owner@example.com": account})
    manager = make_session_manager(MemorySessionStore(), account)

    with pytest.raises(AuthPersistenceError):
        authenticate_user(
            repository,
            manager,
            email="owner@example.com",
            password="correct-password",
        )
