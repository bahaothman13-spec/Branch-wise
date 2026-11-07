"""Authentication workflows that depend on explicit persistence adapters.

This module contains no database model or default store. A production
``AuthRepository`` must use durable persistence and enforce registration
atomicity and case-insensitive email uniqueness. Session durability is provided
by the existing ``SessionStore`` interface in ``session_auth.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from onboarding_rules import OnboardingStatus, get_onboarding_status
from permissions import AuthenticatedPrincipal, is_trusted_principal
from request_validation import is_positive_integer
from security import hash_password, verify_password
from session_auth import IssuedSession, SessionManager, TrustedSessionUser


AUTH_REPOSITORY_EXTENSION = "branchwise.auth_repository"


class AuthPersistenceError(Exception):
    """A persistence adapter could not complete an authentication operation."""


class DuplicateEmailError(AuthPersistenceError):
    """The repository rejected an owner registration for an existing email."""


class InvalidCredentialsError(Exception):
    """Credentials are missing, incorrect, or belong to a disabled account."""


@dataclass(frozen=True)
class AuthAccount:
    """Trusted credential data returned by an authentication repository.

    ``principal`` and ``business_branch_count`` must come from server-side,
    business-scoped data. ``access_version`` is mapped to the existing
    session layer's ``auth_version`` and must change when access is revoked.
    """

    user_id: int
    password_hash: str = field(repr=False)
    access_version: int
    is_active: bool
    is_deleted: bool
    principal: AuthenticatedPrincipal
    business_branch_count: int


class AuthRepository(Protocol):
    """Persistence boundary for owner registration and email lookup.

    Implementations must normalize email case consistently. Registration must
    create one business and one Owner with no branch in one transaction; a
    uniqueness constraint remains authoritative under concurrent requests.
    Production implementations must expose ``is_persistent = True`` and use a
    durable store. There is intentionally no default implementation.
    """

    def find_user_by_email(self, email: str) -> AuthAccount | None:
        """Find the matching account without distinguishing email casing."""

    def register_owner_and_business_atomic(
        self,
        *,
        owner_name: str,
        business_name: str,
        email: str,
        password_hash: str,
    ) -> None:
        """Atomically create the business and its Owner, without a branch."""


@dataclass(frozen=True)
class LoginOutcome:
    """Values needed by the route after successful credential verification."""

    issued_session: IssuedSession
    onboarding: OnboardingStatus


def normalize_email(email: str) -> str:
    """Return the canonical form used for lookup and duplicate detection."""
    return email.strip().casefold()


def _is_valid_auth_account(account: object) -> bool:
    return (
        type(account) is AuthAccount
        and is_positive_integer(account.user_id)
        and isinstance(account.password_hash, str)
        and bool(account.password_hash)
        and type(account.access_version) is int
        and account.access_version >= 0
        and type(account.is_active) is bool
        and type(account.is_deleted) is bool
        and is_trusted_principal(account.principal)
        and type(account.business_branch_count) is int
        and account.business_branch_count >= 0
    )


def install_auth_repository(app, repository: AuthRepository) -> AuthRepository:
    """Explicitly install a repository; never construct an in-memory fallback.

    Development and test apps may inject fakes. Production requires an adapter
    that declares durable persistence; the default app installs no repository.
    """
    if not all(
        callable(getattr(repository, method, None))
        for method in ("find_user_by_email", "register_owner_and_business_atomic")
    ):
        raise TypeError("repository must implement the AuthRepository methods.")
    if (
        app.config.get("APP_ENV") == "production"
        and getattr(repository, "is_persistent", False) is not True
    ):
        raise ValueError("Production authentication requires persistent storage.")

    app.extensions[AUTH_REPOSITORY_EXTENSION] = repository
    return repository


def register_owner(
    repository: AuthRepository,
    *,
    owner_name: str,
    business_name: str,
    email: str,
    password: str,
) -> None:
    """Create an Owner and business atomically, with no automatic branch."""
    canonical_email = normalize_email(email)
    if repository.find_user_by_email(canonical_email) is not None:
        raise DuplicateEmailError("Email is already registered.")

    repository.register_owner_and_business_atomic(
        owner_name=owner_name.strip(),
        business_name=business_name.strip(),
        email=canonical_email,
        password_hash=hash_password(password),
    )


def authenticate_user(
    repository: AuthRepository,
    session_manager: SessionManager,
    *,
    email: str,
    password: str,
) -> LoginOutcome:
    """Verify credentials, issue a server-side session, and report onboarding."""
    account = repository.find_user_by_email(normalize_email(email))
    if account is None:
        raise InvalidCredentialsError("Invalid credentials.")
    if not _is_valid_auth_account(account):
        raise AuthPersistenceError("The repository returned an invalid account.")

    try:
        password_matches = verify_password(password, account.password_hash)
    except Exception as error:
        # Malformed or unreadable stored hashes are an adapter/data failure.
        raise AuthPersistenceError("Stored credentials could not be verified.") from error

    if not password_matches or not account.is_active or account.is_deleted:
        raise InvalidCredentialsError("Invalid credentials.")

    trusted_user = TrustedSessionUser(
        user_id=account.user_id,
        auth_version=account.access_version,
        is_active=account.is_active,
        is_deleted=account.is_deleted,
        principal=account.principal,
    )
    issued = session_manager.create_session(trusted_user)
    onboarding = get_onboarding_status(
        account.principal, branch_count=account.business_branch_count
    )
    return LoginOutcome(issued_session=issued, onboarding=onboarding)


__all__ = [
    "AUTH_REPOSITORY_EXTENSION",
    "AuthAccount",
    "AuthPersistenceError",
    "AuthRepository",
    "DuplicateEmailError",
    "InvalidCredentialsError",
    "LoginOutcome",
    "authenticate_user",
    "install_auth_repository",
    "normalize_email",
    "register_owner",
]
