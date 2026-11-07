"""Repository contracts for user and branch persistence integration.

These are persistence ports only. The application factory installs no default
repository and this module contains no ORM models or fake implementation.
Adapters receive an authenticated server-side session so tenant and actor
identity never come from request fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol

from account_settings_rules import AccountRecord
from permissions import Role
from session_auth import AuthenticatedSession


USER_REPOSITORY_EXTENSION = "branchwise.user_repository"
BRANCH_REPOSITORY_EXTENSION = "branchwise.branch_repository"


class RepositoryError(Exception):
    """Base class for expected repository boundary failures."""


class RepositoryConflictError(RepositoryError):
    """A unique key or state constraint conflicts with the requested change."""


class RepositoryNotFoundError(RepositoryError):
    """A resource is missing or outside the actor's business scope."""


class RepositoryAuthorizationError(RepositoryError):
    """The trusted actor is not authorized for this operation."""


class RepositoryPersistenceError(RepositoryError):
    """A transaction or persistent-store operation could not be completed."""


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True)
class BranchCreateData:
    """Validated API fields for a branch; tenant and creator are server-owned."""

    name: str
    address: str | None = None
    phone: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string.")
        for field_name in ("address", "phone", "notes"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or None.")


@dataclass(frozen=True)
class BranchUpdateData:
    """Partial branch update; omitted keys are unchanged and None clears text."""

    changes: Mapping[str, str | None]

    def __post_init__(self) -> None:
        if not isinstance(self.changes, Mapping) or not self.changes:
            raise ValueError("changes must be a non-empty mapping.")
        allowed = {"name", "address", "phone", "notes"}
        if set(self.changes) - allowed:
            raise ValueError("changes contains unsupported fields.")
        values = dict(self.changes)
        if "name" in values and (
            not isinstance(values["name"], str) or not values["name"].strip()
        ):
            raise ValueError("name must be a non-empty string when supplied.")
        for field_name in ("address", "phone", "notes"):
            if (
                field_name in values
                and values[field_name] is not None
                and not isinstance(values[field_name], str)
            ):
                raise TypeError(f"{field_name} must be a string or None.")
        object.__setattr__(self, "changes", MappingProxyType(values))


@dataclass(frozen=True)
class BranchRecord:
    """Safe branch data returned from persistence; contains no ORM instance."""

    branch_id: int
    business_id: int
    name: str
    address: str | None
    phone: str | None
    notes: str | None
    is_active: bool
    created_by: int

    def __post_init__(self) -> None:
        if not all(
            _positive_id(value)
            for value in (self.branch_id, self.business_id, self.created_by)
        ):
            raise ValueError(
                "branch, business, and creator IDs must be positive integers."
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string.")
        if type(self.is_active) is not bool:
            raise TypeError("is_active must be a Boolean.")


@dataclass(frozen=True)
class UserCreateData:
    """Validated staff creation data; business and creator come from the actor."""

    full_name: str
    email: str
    role: Role
    branch_id: int
    password_hash: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.full_name, str) or not self.full_name.strip():
            raise ValueError("full_name must be a non-empty string.")
        if not isinstance(self.email, str) or not self.email.strip():
            raise ValueError("email must be a non-empty string.")
        if type(self.role) is not Role or self.role not in {
            Role.BRANCH_MANAGER,
            Role.EMPLOYEE,
        }:
            raise ValueError("Only branch managers and employees can be created here.")
        if not _positive_id(self.branch_id):
            raise ValueError("branch_id must be a positive integer.")
        if not isinstance(self.password_hash, str) or not self.password_hash:
            raise ValueError("password_hash must be a non-empty string.")


@dataclass(frozen=True)
class UserRecord:
    """Safe persisted user data; password hashes never cross this contract."""

    user_id: int
    business_id: int
    branch_id: int | None
    full_name: str
    email: str
    role: Role
    is_active: bool
    access_version: int

    def __post_init__(self) -> None:
        if not _positive_id(self.user_id) or not _positive_id(self.business_id):
            raise ValueError("user_id and business_id must be positive integers.")
        if self.branch_id is not None and not _positive_id(self.branch_id):
            raise ValueError("branch_id must be positive or None for an Owner.")
        if not isinstance(self.full_name, str) or not self.full_name.strip():
            raise ValueError("full_name must be a non-empty string.")
        if not isinstance(self.email, str) or not self.email.strip():
            raise ValueError("email must be a non-empty string.")
        if type(self.role) is not Role:
            raise TypeError("role must be a Role.")
        if (self.role is Role.OWNER) != (self.branch_id is None):
            raise ValueError("Only Owners may have no branch assignment.")
        if type(self.is_active) is not bool:
            raise TypeError("is_active must be a Boolean.")
        if type(self.access_version) is not int or self.access_version < 0:
            raise ValueError("access_version must be a non-negative integer.")


class BranchRepository(Protocol):
    """Tenant-safe persistence boundary for owner branch management.

    Each implementation must authorize the trusted actor, derive business_id
    from actor.principal, and include that business_id in every resource query.
    Create/update/deactivate operations must each commit atomically. Name
    uniqueness is case-insensitive within the business and concurrency-safe.
    Missing and cross-business IDs should both raise RepositoryNotFoundError.
    """

    def list_branches(self, *, actor: AuthenticatedSession) -> tuple[BranchRecord, ...]:
        """List only branches the actor is authorized to see in their business."""

    def get_branch(
        self, *, actor: AuthenticatedSession, branch_id: int
    ) -> BranchRecord:
        """Load a scoped branch or raise RepositoryNotFoundError."""

    def create_branch_atomic(
        self, *, actor: AuthenticatedSession, data: BranchCreateData
    ) -> BranchRecord:
        """Create the branch with server-derived business and owner IDs."""

    def update_branch_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        branch_id: int,
        data: BranchUpdateData,
    ) -> BranchRecord:
        """Update a scoped branch or raise not-found/conflict errors."""

    def deactivate_branch_atomic(
        self, *, actor: AuthenticatedSession, branch_id: int
    ) -> BranchRecord:
        """Soft-deactivate a scoped branch or raise not-found."""


class UserRepository(Protocol):
    """Tenant-safe persistence boundary for owner-managed user accounts.

    Every method must verify the trusted actor's authorization and scope target
    reads/writes to actor.principal.business_id. Creation, assignment/status
    changes, and password changes are atomic. Changes to role, branch, password,
    or active status increment users.access_version, append the matching audit
    event, and revoke that user's sessions in the same transaction. A successful
    change therefore invalidates sessions whose saved access_version_at_login
    no longer matches the current users.access_version.
    """

    def list_users(self, *, actor: AuthenticatedSession) -> tuple[UserRecord, ...]:
        """List only accounts belonging to the actor's business."""

    def get_own_account(self, *, actor: AuthenticatedSession) -> AccountRecord:
        """Load the actor's trusted credential row for existing account rules."""

    def create_user_atomic(
        self, *, actor: AuthenticatedSession, data: UserCreateData
    ) -> UserRecord:
        """Create a staff user and CREATE audit event in one transaction."""

    def set_user_active_atomic(
        self, *, actor: AuthenticatedSession, user_id: int, is_active: bool
    ) -> UserRecord:
        """Change active state, bump access_version, audit, and revoke sessions."""

    def assign_user_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        user_id: int,
        role: Role,
        branch_id: int,
    ) -> UserRecord:
        """Change role/branch only after verifying the branch is same-business."""

    def change_own_password_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        new_password_hash: str,
    ) -> None:
        """CAS against actor.access_version_at_login, then bump, audit, revoke."""


def _install_repository(
    app, repository, *, extension: str, methods: tuple[str, ...]
):
    if not all(callable(getattr(repository, method, None)) for method in methods):
        raise TypeError("repository does not implement the required contract.")
    if app.config.get("APP_ENV") == "production" and any(
        getattr(repository, attribute, False) is not True
        for attribute in ("is_persistent", "is_shared")
    ):
        raise ValueError(
            "Production repositories must be persistent and shared across workers."
        )
    app.extensions[extension] = repository
    return repository


def install_branch_repository(app, repository: BranchRepository) -> BranchRepository:
    """Explicitly install a branch adapter; there is no fallback adapter."""
    return _install_repository(
        app,
        repository,
        extension=BRANCH_REPOSITORY_EXTENSION,
        methods=(
            "list_branches",
            "get_branch",
            "create_branch_atomic",
            "update_branch_atomic",
            "deactivate_branch_atomic",
        ),
    )


def install_user_repository(app, repository: UserRepository) -> UserRepository:
    """Explicitly install a user adapter; there is no fallback adapter."""
    return _install_repository(
        app,
        repository,
        extension=USER_REPOSITORY_EXTENSION,
        methods=(
            "list_users",
            "get_own_account",
            "create_user_atomic",
            "set_user_active_atomic",
            "assign_user_atomic",
            "change_own_password_atomic",
        ),
    )


__all__ = [
    "BRANCH_REPOSITORY_EXTENSION",
    "USER_REPOSITORY_EXTENSION",
    "BranchCreateData",
    "BranchRecord",
    "BranchRepository",
    "BranchUpdateData",
    "RepositoryAuthorizationError",
    "RepositoryConflictError",
    "RepositoryError",
    "RepositoryNotFoundError",
    "RepositoryPersistenceError",
    "UserCreateData",
    "UserRecord",
    "UserRepository",
    "install_branch_repository",
    "install_user_repository",
]
