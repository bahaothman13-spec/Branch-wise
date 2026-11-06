"""Pure validation and authorization rules for shared business categories.

Callers provide category records loaded from trusted server-side data. These
helpers return proposals only; they do not persist categories, rewrite product
references, or change inventory. Category names are normalized by trimming
surrounding whitespace and compared with Python's ``casefold`` within one
business. Any future MySQL uniqueness constraint must reproduce that same
normalization; MySQL collation rules can compare names differently.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from isolation_rules import require_resource_access
from permissions import (
    PRODUCT_CATALOG_MANAGE,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    is_trusted_principal,
    is_valid_resource_scope,
)
from request_validation import is_positive_integer

CATEGORY_NAME_MAX_LENGTH = 120


class CategoryAction(str, Enum):
    """Supported category changes. Categories are deactivated, never deleted."""

    CREATE = "create"
    MODIFY = "modify"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


@dataclass(frozen=True)
class CategoryContext:
    """A server-loaded category and its business-wide scope."""

    category_id: int
    name: str
    scope: ResourceScope
    is_active: bool = True


@dataclass(frozen=True)
class CategoryData:
    """Normalized category metadata proposed for creation or modification."""

    name: str


@dataclass(frozen=True)
class CategoryProposal:
    """An in-memory category proposal for direct Owner management."""

    data: CategoryData


@dataclass(frozen=True)
class CategoryStatusProposal:
    """An activation proposal that preserves category and product history."""

    is_active: bool


_ALLOWED_CATEGORY_FIELDS = frozenset({"name"})


def _validate_name(value):
    if not isinstance(value, str):
        raise ValueError("name must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("name is required.")
    if len(normalized) > CATEGORY_NAME_MAX_LENGTH:
        raise ValueError(
            f"name must be at most {CATEGORY_NAME_MAX_LENGTH} characters."
        )
    return normalized


def _name_key(value):
    return _validate_name(value).casefold()


def _validate_category_context(category_id, category):
    if not is_positive_integer(category_id):
        raise ValueError("category_id must be a positive integer.")
    if type(category) is not CategoryContext:
        raise ValueError("A trusted category context is required.")
    if not is_positive_integer(category.category_id):
        raise ValueError("The trusted category has an invalid category_id.")
    if category.category_id != category_id:
        raise ValueError("The trusted category does not match category_id.")
    if not is_valid_resource_scope(category.scope) or category.scope.branch_id is not None:
        raise ValueError("A trusted business-wide category scope is required.")
    if type(category.is_active) is not bool:
        raise ValueError("The trusted category has an invalid is_active value.")
    _validate_name(category.name)


def _category_records(categories):
    if isinstance(categories, (str, bytes, Mapping)) or not isinstance(
        categories, Iterable
    ):
        raise ValueError("categories must be an iterable of trusted category contexts.")
    for category in categories:
        if type(category) is not CategoryContext:
            raise ValueError("categories must contain trusted CategoryContext values.")
        _validate_category_context(category.category_id, category)
        yield category


def ensure_category_name_available(
    business_id, name, categories=(), *, exclude_category_id=None
):
    """Reject a normalized duplicate name in this business, allowing other businesses."""
    if not is_positive_integer(business_id):
        raise ValueError("business_id must be a positive integer.")
    normalized = _validate_name(name)
    key = _name_key(normalized)
    if exclude_category_id is not None and not is_positive_integer(exclude_category_id):
        raise ValueError("exclude_category_id must be a positive integer.")

    for category in _category_records(categories):
        if category.category_id == exclude_category_id:
            continue
        if (
            category.scope.business_id == business_id
            and _name_key(category.name) == key
        ):
            raise ValueError("name is already used in this business.")
    return normalized


def _as_action(action):
    try:
        return action if isinstance(action, CategoryAction) else CategoryAction(action)
    except (TypeError, ValueError):
        raise ValueError("Unknown category catalog action.") from None


def _authorize(principal, action, *, category_id=None, category=None):
    action = _as_action(action)
    if not is_trusted_principal(principal):
        raise PermissionError("You are not allowed to manage the product catalog.")
    if principal.role is not Role.OWNER:
        raise PermissionError("Only an authorized Owner may manage categories.")

    if action is CategoryAction.CREATE:
        if category_id is not None or category is not None:
            raise ValueError("Category creation cannot use an existing category scope.")
        scope = ResourceScope(principal.business_id, None)
    else:
        _validate_category_context(category_id, category)
        scope = category.scope

    require_resource_access(principal, PRODUCT_CATALOG_MANAGE, scope)
    return


def validate_category_creation(
    principal,
    *,
    name,
    existing_categories=(),
):
    """Validate a category for the authenticated business without persisting it."""
    _authorize(principal, CategoryAction.CREATE)
    data = CategoryData(
        name=ensure_category_name_available(
            principal.business_id, name, existing_categories
        )
    )
    return CategoryProposal(data=data)


def validate_category_modification(
    principal,
    *,
    category_id,
    category,
    changes,
    existing_categories=(),
):
    """Validate a partial category metadata update without changing references."""
    _authorize(
        principal,
        CategoryAction.MODIFY,
        category_id=category_id,
        category=category,
    )
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("At least one category field change is required.")
    if set(changes) - _ALLOWED_CATEGORY_FIELDS:
        raise ValueError("Category changes contain unsupported fields.")

    proposed_name = changes.get("name", category.name)
    name = ensure_category_name_available(
        category.scope.business_id,
        proposed_name,
        existing_categories,
        exclude_category_id=category_id,
    )
    return CategoryProposal(data=CategoryData(name=name))


def validate_category_status_change(
    principal,
    *,
    category_id,
    category,
    is_active,
):
    """Validate an activation toggle without deleting history or changing stock."""
    if type(is_active) is not bool:
        raise ValueError("is_active must be a boolean.")
    action = CategoryAction.ACTIVATE if is_active else CategoryAction.DEACTIVATE
    _authorize(
        principal,
        action,
        category_id=category_id,
        category=category,
    )
    return CategoryStatusProposal(is_active=is_active)


def validate_category_activation(
    principal,
    *,
    category_id,
    category,
):
    """Validate category activation by an authorized Owner."""
    return validate_category_status_change(
        principal,
        category_id=category_id,
        category=category,
        is_active=True,
    )


def validate_category_deactivation(
    principal,
    *,
    category_id,
    category,
):
    """Validate safe category deactivation while retaining product references."""
    return validate_category_status_change(
        principal,
        category_id=category_id,
        category=category,
        is_active=False,
    )


__all__ = [
    "CATEGORY_NAME_MAX_LENGTH",
    "CategoryAction",
    "CategoryContext",
    "CategoryData",
    "CategoryProposal",
    "CategoryStatusProposal",
    "ensure_category_name_available",
    "validate_category_activation",
    "validate_category_creation",
    "validate_category_deactivation",
    "validate_category_modification",
    "validate_category_status_change",
]
