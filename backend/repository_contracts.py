"""Repository contracts for account, business, branch, and catalog persistence.

These are persistence ports only. The application factory installs no default
repository and this module contains no ORM models or fake implementation.
Adapters receive an authenticated server-side session so tenant and actor
identity never come from request fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping, Protocol

from account_settings_rules import AccountRecord, BusinessSettingsRecord
from category_rules import CategoryContext, CategoryData
from product_rules import (
    PRODUCT_CODE_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    CatalogCodeEntry,
    ProductCategoryContext,
    ProductData,
)
from variant_rules import ProductVariantContext, VariantData
from permissions import Role
from session_auth import AuthenticatedSession


USER_REPOSITORY_EXTENSION = "branchwise.user_repository"
BRANCH_REPOSITORY_EXTENSION = "branchwise.branch_repository"
BUSINESS_SETTINGS_REPOSITORY_EXTENSION = "branchwise.business_settings_repository"
PRODUCT_REPOSITORY_EXTENSION = "branchwise.product_repository"
CATEGORY_VARIANT_REPOSITORY_EXTENSION = "branchwise.category_variant_repository"
OPERATIONS_REPOSITORY_EXTENSION = "branchwise.operations_repository"


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
class AccountProfileUpdateData:
    """Validated self-service profile changes; identity is always server-owned."""

    changes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.changes, Mapping) or not self.changes:
            raise ValueError("changes must be a non-empty mapping.")
        values = dict(self.changes)
        if set(values) - {"name", "email"}:
            raise ValueError("changes contains unsupported profile fields.")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in values.values()
        ):
            raise ValueError("profile changes must be non-empty strings.")
        object.__setattr__(self, "changes", MappingProxyType(values))


@dataclass(frozen=True)
class BusinessSettingsUpdateData:
    """Validated business name/contact updates without a client-supplied tenant."""

    changes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.changes, Mapping) or not self.changes:
            raise ValueError("changes must be a non-empty mapping.")
        values = dict(self.changes)
        if set(values) - {"business_name", "email"}:
            raise ValueError("changes contains unsupported business settings fields.")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in values.values()
        ):
            raise ValueError("business settings changes must be non-empty strings.")
        object.__setattr__(self, "changes", MappingProxyType(values))


@dataclass(frozen=True)
class ProductRecord:
    """Safe catalog metadata returned by persistence, without an ORM object."""

    product_id: int
    business_id: int
    data: ProductData
    is_active: bool

    def __post_init__(self) -> None:
        if not _positive_id(self.product_id) or not _positive_id(self.business_id):
            raise ValueError("product_id and business_id must be positive integers.")
        if type(self.data) is not ProductData:
            raise TypeError("data must be validated ProductData.")
        if type(self.data.category_id) is not int or self.data.category_id <= 0:
            raise ValueError("category_id must be a positive integer.")
        for field_name, value, max_length in (
            ("name", self.data.name, PRODUCT_NAME_MAX_LENGTH),
            ("product_code", self.data.product_code, PRODUCT_CODE_MAX_LENGTH),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string.")
            if len(value.strip()) > max_length:
                raise ValueError(f"{field_name} exceeds its maximum length.")
        for field_name, value in (
            ("selling_price", self.data.selling_price),
            ("cost_price", self.data.cost_price),
        ):
            if not isinstance(value, Decimal):
                raise TypeError("Product prices must be Decimal values.")
            if not value.is_finite() or value < 0 or value > Decimal("99999999999.999"):
                raise ValueError(f"{field_name} is outside the supported range.")
            try:
                normalized = value.quantize(Decimal("0.001"))
            except InvalidOperation:
                raise ValueError(f"{field_name} is outside the supported range.") from None
            if normalized != value:
                raise ValueError(f"{field_name} has unsupported precision.")
        if type(self.is_active) is not bool:
            raise TypeError("is_active must be a Boolean.")


@dataclass(frozen=True)
class ProductListResult:
    """One page of catalog rows and its filtered total count."""

    products: tuple[ProductRecord, ...]
    total_items: int

    def __post_init__(self) -> None:
        if type(self.products) is not tuple or any(
            type(product) is not ProductRecord for product in self.products
        ):
            raise TypeError("products must be a tuple of ProductRecord values.")
        if type(self.total_items) is not int or self.total_items < len(self.products):
            raise ValueError("total_items must cover the returned page.")


@dataclass(frozen=True)
class CategoryListResult:
    """One page of same-business categories and its filtered total count."""

    categories: tuple[CategoryContext, ...]
    total_items: int

    def __post_init__(self) -> None:
        if type(self.categories) is not tuple or any(
            type(category) is not CategoryContext for category in self.categories
        ):
            raise TypeError("categories must be a tuple of CategoryContext values.")
        if type(self.total_items) is not int or self.total_items < len(self.categories):
            raise ValueError("total_items must cover the returned page.")


@dataclass(frozen=True)
class ProductVariantRecord:
    """Safe persisted variant metadata, without inventory or ORM objects."""

    variant_id: int
    business_id: int
    data: VariantData
    is_active: bool

    def __post_init__(self) -> None:
        if not _positive_id(self.variant_id) or not _positive_id(self.business_id):
            raise ValueError("variant_id and business_id must be positive integers.")
        if type(self.data) is not VariantData:
            raise TypeError("data must be validated VariantData.")
        if not _positive_id(self.data.product_id):
            raise ValueError("product_id must be a positive integer.")
        if type(self.is_active) is not bool:
            raise TypeError("is_active must be a Boolean.")


@dataclass(frozen=True)
class ProductVariantListResult:
    """One page of variants belonging to one business product."""

    variants: tuple[ProductVariantRecord, ...]
    total_items: int

    def __post_init__(self) -> None:
        if type(self.variants) is not tuple or any(
            type(variant) is not ProductVariantRecord for variant in self.variants
        ):
            raise TypeError("variants must be a tuple of ProductVariantRecord values.")
        if type(self.total_items) is not int or self.total_items < len(self.variants):
            raise ValueError("total_items must cover the returned page.")


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

    def update_own_profile_atomic(
        self, *, actor: AuthenticatedSession, data: AccountProfileUpdateData
    ) -> AccountRecord:
        """Update only the actor's name/email and audit the change atomically.

        The adapter must scope the update to actor.user_id and the actor's
        business. Email uniqueness conflicts raise RepositoryConflictError.
        """

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


class BusinessSettingsRepository(Protocol):
    """Tenant-safe persistence boundary for Owner business settings.

    Every operation must require the Owner's trusted business-settings grant
    and derive the target business from actor.principal.business_id. Reads and
    updates must include that business ID in their predicates; updates commit
    atomically and record the settings change.
    """

    def get_own_business_settings(
        self, *, actor: AuthenticatedSession
    ) -> BusinessSettingsRecord:
        """Load the actor Owner's business name and existing contact email."""

    def update_own_business_settings_atomic(
        self, *, actor: AuthenticatedSession, data: BusinessSettingsUpdateData
    ) -> BusinessSettingsRecord:
        """Update scoped settings or raise a conflict/persistence error."""


class ProductRepository(Protocol):
    """Tenant-safe persistence boundary for the shared product catalog.

    All product and category lookups must include actor.principal.business_id.
    Product codes are unique case-insensitively across products and variants
    within that business. Create/update transactions must recheck uniqueness
    so concurrent requests cannot bypass the rule. Creation must atomically
    create the product together with the required default variant from the
    approved catalog schema. Product updates and status changes preserve
    transaction and inventory history.
    Missing and cross-business IDs raise RepositoryNotFoundError.
    """

    def list_products(
        self,
        *,
        actor: AuthenticatedSession,
        search: str | None,
        category_id: int | None,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> ProductListResult:
        """Return filtered same-business rows and the total before pagination.

        Search matches product name or product_code. Filtering occurs before
        pagination, and catalog rows are not split by branch inventory.
        """

    def get_product(
        self, *, actor: AuthenticatedSession, product_id: int
    ) -> ProductRecord:
        """Load one product in the actor's business or raise not-found."""

    def get_category(
        self, *, actor: AuthenticatedSession, category_id: int
    ) -> ProductCategoryContext:
        """Load a trusted category and business-wide scope for validation."""

    def list_catalog_codes(
        self, *, actor: AuthenticatedSession
    ) -> tuple[CatalogCodeEntry, ...]:
        """Load this business's product and variant codes for rule validation."""

    def create_product_atomic(
        self, *, actor: AuthenticatedSession, data: ProductData
    ) -> ProductRecord:
        """Create catalog metadata and its required default variant atomically."""

    def update_product_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        product_id: int,
        data: ProductData,
    ) -> ProductRecord:
        """Update business-scoped metadata while preserving related history."""

    def set_product_active_atomic(
        self, *, actor: AuthenticatedSession, product_id: int, is_active: bool
    ) -> ProductRecord:
        """Change catalog status without deleting product or history rows."""


class CategoryVariantRepository(ProductRepository, Protocol):
    """Persistence port for business categories and product variants.

    Implementations also satisfy ProductRepository so product/category/code
    lookups share one tenant-safe catalog adapter. Every lookup derives the
    business from the authenticated actor. SKU checks must be business-scoped,
    case-insensitive, checked against the approved catalog code registry, and
    repeated inside each create/update transaction to close race conditions.
    Category names are trimmed and unique by casefold within a business; the
    uniqueness check must be repeated inside create/update transactions.
    Category updates/status changes and variant metadata/status changes must
    preserve all product, stock, and transaction history. No method deletes a
    category or variant.
    """

    def list_categories(
        self,
        *,
        actor: AuthenticatedSession,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> CategoryListResult:
        """List this business's categories, filtering before pagination."""

    def get_category_context(
        self, *, actor: AuthenticatedSession, category_id: int
    ) -> CategoryContext:
        """Load a named category context or raise not-found when out of scope."""

    def list_all_categories(
        self, *, actor: AuthenticatedSession
    ) -> tuple[CategoryContext, ...]:
        """Load all same-business categories for normalized-name validation."""

    def create_category_atomic(
        self, *, actor: AuthenticatedSession, data: CategoryData
    ) -> CategoryContext:
        """Create a category with server-derived business and active status."""

    def update_category_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        category_id: int,
        data: CategoryData,
    ) -> CategoryContext:
        """Update same-business category metadata or raise conflict/not-found."""

    def set_category_active_atomic(
        self, *, actor: AuthenticatedSession, category_id: int, is_active: bool
    ) -> CategoryContext:
        """Change active status without deleting referenced category history."""

    def list_product_variants(
        self,
        *,
        actor: AuthenticatedSession,
        product_id: int,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> ProductVariantListResult:
        """List one same-business product's variants before pagination."""

    def get_product_variant(
        self,
        *,
        actor: AuthenticatedSession,
        product_id: int,
        variant_id: int,
    ) -> ProductVariantRecord:
        """Load a variant scoped to the requested same-business product."""

    def list_product_variant_contexts(
        self, *, actor: AuthenticatedSession, product_id: int
    ) -> tuple[ProductVariantContext, ...]:
        """Load trusted variant contexts for default-variant validation."""

    def catalog_code_exists(
        self,
        *,
        actor: AuthenticatedSession,
        code: str,
        exclude_resource_type: str | None = None,
        exclude_resource_id: int | None = None,
    ) -> bool:
        """Check SKU/code use inside the actor's business, optionally excluding itself.

        Product and variant create/update transactions must repeat this
        case-insensitive check against the shared registry before committing.
        """

    def create_variant_atomic(
        self, *, actor: AuthenticatedSession, data: VariantData
    ) -> ProductVariantRecord:
        """Create a variant and code-registry row atomically."""

    def update_variant_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        product_id: int,
        variant_id: int,
        data: VariantData,
    ) -> ProductVariantRecord:
        """Update variant metadata and its registered SKU atomically."""

    def set_variant_active_atomic(
        self,
        *,
        actor: AuthenticatedSession,
        product_id: int,
        variant_id: int,
        is_active: bool,
    ) -> ProductVariantRecord:
        """Change variant status without deleting inventory or transaction rows."""


class OperationsRepository(Protocol):
    """Persistence port for inventory, sales, returns, and approval workflows.

    Every context loader derives business and branch identity from ``actor``;
    all writes revalidate under transaction locks. Atomic write methods persist
    the domain event, inventory ledger effects, idempotency record, and result
    together. Implementations must use the shared, deterministically ordered
    inventory locks described in ``TRANSACTION_SAFETY_CONTRACT.md``.
    """

    def get_product_overview_inputs(
        self, *, actor: AuthenticatedSession, product_id: int, branch_id: int | None
    ) -> Mapping[str, object]:
        """Return trusted records consumed by ``build_product_overview``."""

    def get_inventory_adjustment_inputs(
        self, *, actor: AuthenticatedSession, branch_id: int, variant_id: int
    ) -> Mapping[str, object]:
        """Return trusted branch, product, variant, and balance contexts."""

    def create_manual_adjustment_atomic(
        self, *, actor: AuthenticatedSession, movement: object, idempotency_request: object
    ) -> Mapping[str, object]:
        """Append one adjustment and its effect key atomically; never overwrite history."""

    def submit_product_request_atomic(
        self, *, actor: AuthenticatedSession, request: object,
        reason: str, requested_payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Save an immutable request snapshot and pending status atomically."""

    def get_product_request_submission_context(
        self, *, actor: AuthenticatedSession
    ) -> Mapping[str, object]:
        """Load the active assigned branch and scoped business branch count."""

    def get_product_request(
        self, *, actor: AuthenticatedSession, request_id: int
    ) -> Mapping[str, object]:
        """Load a request scoped to the actor's business and authorized branch."""

    def list_product_requests(
        self, *, actor: AuthenticatedSession, status: str | None
    ) -> tuple[Mapping[str, object], ...]:
        """List only requests visible to the role and branch of ``actor``."""

    def comment_product_request_atomic(
        self, *, actor: AuthenticatedSession, request_id: int, comment: str
    ) -> Mapping[str, object]:
        """Append an in-scope manager comment without deciding the request."""

    def decide_product_request_atomic(
        self, *, actor: AuthenticatedSession, request_id: int,
        approved: bool, rejection_reason: str | None
    ) -> Mapping[str, object]:
        """Revalidate snapshot and atomically apply catalog change plus decision."""

    def get_sale_creation_inputs(
        self, *, actor: AuthenticatedSession, branch_id: int, items: object
    ) -> Mapping[str, object]:
        """Load active branch, product/variant prices, and locked stock snapshots."""

    def create_sale_atomic(
        self, *, actor: AuthenticatedSession, proposal: object,
        idempotency_request: object
    ) -> Mapping[str, object]:
        """Create sale snapshots and ledger deductions in one idempotent transaction."""

    def list_sales(self, *, actor: AuthenticatedSession) -> tuple[object, ...]:
        """Load same-business sales for the pure history filtering rules."""

    def get_sale_cancellation_inputs(
        self, *, actor: AuthenticatedSession, sale_id: int
    ) -> Mapping[str, object]:
        """Load sale, pending requests, and linked return history in tenant scope."""

    def get_cancellation_decision_inputs(
        self, *, actor: AuthenticatedSession, request_id: int
    ) -> Mapping[str, object]:
        """Load a pending request, its sale, and linked returns for decision rules."""

    def request_sale_cancellation_atomic(
        self, *, actor: AuthenticatedSession, request: object
    ) -> Mapping[str, object]:
        """Persist a unique pending cancellation request atomically."""

    def decide_sale_cancellation_atomic(
        self, *, actor: AuthenticatedSession, request: object,
        sale: object, restoration_quantities: Mapping[int, int], approved: bool,
        rejection_reason: str | None, idempotency_request: object | None
    ) -> Mapping[str, object]:
        """Decide once and restore only unreturned sold stock on approval."""

    def list_cancellation_requests(
        self, *, actor: AuthenticatedSession, status: str | None
    ) -> tuple[Mapping[str, object], ...]:
        """List cancellation requests visible to the actor's role and branch."""

    def get_return_submission_inputs(
        self, *, actor: AuthenticatedSession, sale_id: int | None, branch_id: int | None
    ) -> Mapping[str, object]:
        """Load a scoped sale or branch plus cumulative return history."""

    def list_returns(
        self, *, actor: AuthenticatedSession, status: str | None
    ) -> tuple[Mapping[str, object], ...]:
        """List returns visible to the actor's role and branch."""

    def submit_return_atomic(
        self, *, actor: AuthenticatedSession, request: object,
        idempotency_request: object
    ) -> Mapping[str, object]:
        """Persist return quantities and hold them unavailable for inspection."""

    def get_return_inspection_inputs(
        self, *, actor: AuthenticatedSession, return_id: int
    ) -> Mapping[str, object]:
        """Load return lines, prior inspections, and a transaction-safe inspection ID."""

    def verify_unlinked_return_atomic(
        self, *, actor: AuthenticatedSession, return_id: int, return_request: object
    ) -> Mapping[str, object]:
        """Persist manager/Owner verification before inspection of an unlinked return."""

    def inspect_return_atomic(
        self, *, actor: AuthenticatedSession, return_request: object,
        returned_items: object, inspection: object, changes: object,
        idempotency_request: object
    ) -> Mapping[str, object]:
        """Persist inspection and only the validated resalable/damaged stock effects."""


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
            "update_own_profile_atomic",
            "create_user_atomic",
            "set_user_active_atomic",
            "assign_user_atomic",
            "change_own_password_atomic",
        ),
    )


def install_business_settings_repository(
    app, repository: BusinessSettingsRepository
) -> BusinessSettingsRepository:
    """Explicitly install a business-settings adapter; no fallback is used."""
    return _install_repository(
        app,
        repository,
        extension=BUSINESS_SETTINGS_REPOSITORY_EXTENSION,
        methods=(
            "get_own_business_settings",
            "update_own_business_settings_atomic",
        ),
    )


def install_product_repository(app, repository: ProductRepository) -> ProductRepository:
    """Explicitly install a product adapter; no fallback persistence is used."""
    category_repository = app.extensions.get(CATEGORY_VARIANT_REPOSITORY_EXTENSION)
    if category_repository is not None and category_repository is not repository:
        raise ValueError("Product and category/variant APIs must share one catalog adapter.")
    return _install_repository(
        app,
        repository,
        extension=PRODUCT_REPOSITORY_EXTENSION,
        methods=(
            "list_products",
            "get_product",
            "get_category",
            "list_catalog_codes",
            "create_product_atomic",
            "update_product_atomic",
            "set_product_active_atomic",
        ),
    )


def install_category_variant_repository(
    app, repository: CategoryVariantRepository
) -> CategoryVariantRepository:
    """Install one adapter for both BE-15 products and BE-16 catalog APIs."""
    product_repository = app.extensions.get(PRODUCT_REPOSITORY_EXTENSION)
    if product_repository is not None and product_repository is not repository:
        raise ValueError("Product and category/variant APIs must share one catalog adapter.")
    installed = _install_repository(
        app,
        repository,
        extension=CATEGORY_VARIANT_REPOSITORY_EXTENSION,
        methods=(
            "list_products",
            "get_product",
            "get_category",
            "list_catalog_codes",
            "create_product_atomic",
            "update_product_atomic",
            "set_product_active_atomic",
            "list_categories",
            "get_category_context",
            "list_all_categories",
            "create_category_atomic",
            "update_category_atomic",
            "set_category_active_atomic",
            "list_product_variants",
            "get_product_variant",
            "list_product_variant_contexts",
            "catalog_code_exists",
            "create_variant_atomic",
            "update_variant_atomic",
            "set_variant_active_atomic",
        ),
    )
    app.extensions[PRODUCT_REPOSITORY_EXTENSION] = repository
    return installed


def install_operations_repository(app, repository: OperationsRepository) -> OperationsRepository:
    """Explicitly install the transactional operations adapter; no fallback."""
    return _install_repository(
        app,
        repository,
        extension=OPERATIONS_REPOSITORY_EXTENSION,
        methods=(
            "get_product_overview_inputs", "get_inventory_adjustment_inputs",
            "create_manual_adjustment_atomic", "submit_product_request_atomic",
            "get_product_request_submission_context",
            "get_product_request", "list_product_requests",
            "comment_product_request_atomic", "decide_product_request_atomic",
            "get_sale_creation_inputs", "create_sale_atomic", "list_sales",
            "get_sale_cancellation_inputs", "get_cancellation_decision_inputs",
            "request_sale_cancellation_atomic", "decide_sale_cancellation_atomic",
            "list_cancellation_requests",
            "get_return_submission_inputs", "submit_return_atomic",
            "list_returns",
            "get_return_inspection_inputs", "inspect_return_atomic",
            "verify_unlinked_return_atomic",
        ),
    )


__all__ = [
    "AccountProfileUpdateData",
    "AccountRecord",
    "CATEGORY_VARIANT_REPOSITORY_EXTENSION",
    "OPERATIONS_REPOSITORY_EXTENSION",
    "CategoryListResult",
    "CategoryVariantRepository",
    "BUSINESS_SETTINGS_REPOSITORY_EXTENSION",
    "BRANCH_REPOSITORY_EXTENSION",
    "USER_REPOSITORY_EXTENSION",
    "BranchCreateData",
    "BranchRecord",
    "BranchRepository",
    "BranchUpdateData",
    "BusinessSettingsRepository",
    "BusinessSettingsRecord",
    "BusinessSettingsUpdateData",
    "PRODUCT_REPOSITORY_EXTENSION",
    "ProductListResult",
    "ProductRecord",
    "ProductVariantListResult",
    "ProductVariantRecord",
    "ProductRepository",
    "OperationsRepository",
    "RepositoryAuthorizationError",
    "RepositoryConflictError",
    "RepositoryError",
    "RepositoryNotFoundError",
    "RepositoryPersistenceError",
    "UserCreateData",
    "UserRecord",
    "UserRepository",
    "install_branch_repository",
    "install_business_settings_repository",
    "install_category_variant_repository",
    "install_product_repository",
    "install_operations_repository",
    "install_user_repository",
]
