"""Pure business rules for the shared, business-wide product catalog.

This module validates in-memory proposals only. It does not create products,
variants, or inventory records. Product creation in the database layer must
insert a product and at least one validated default variant atomically.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re

from isolation_rules import require_resource_access, resources_belong_to_business
from permissions import (
    PRODUCT_CATALOG_MANAGE,
    AuthenticatedPrincipal,
    ResourceScope,
    is_trusted_principal,
)
from product_request_rules import (
    ProductChangeType,
    ProductRequest,
    validate_request_submission,
)
from request_validation import is_positive_integer


PRODUCT_NAME_MAX_LENGTH = 180
PRODUCT_CODE_MAX_LENGTH = 80
_MONEY_QUANTUM = Decimal("0.001")
_MONEY_MAX = Decimal("99999999999.999")
_DEFAULT_COST_PRICE = Decimal("0")
_DECIMAL_TEXT = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)


class ProductAction(str, Enum):
    """Supported catalog changes. Permanent deletion is deliberately absent."""

    CREATE = "create"
    MODIFY = "modify"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    DELETE = "delete"


@dataclass(frozen=True)
class ProductCategoryContext:
    """A category ID and scope from the same server-loaded category record."""

    category_id: int
    scope: ResourceScope
    is_active: bool = True


@dataclass(frozen=True)
class CatalogCodeEntry:
    """One row from the shared product-and-variant ``catalog_codes`` registry."""

    business_id: int
    code: str
    resource_type: str
    resource_id: int


@dataclass(frozen=True)
class ProductData:
    """Validated product metadata, with prices normalized to three decimals."""

    name: str
    product_code: str
    category_id: int
    selling_price: Decimal
    cost_price: Decimal = _DEFAULT_COST_PRICE


@dataclass(frozen=True)
class ProductProposal:
    """Validated metadata and an in-memory employee request, if applicable."""

    data: ProductData
    request: ProductRequest | None = None


@dataclass(frozen=True)
class ProductStatusProposal:
    """Proposed active state and an in-memory employee request, if applicable."""

    is_active: bool
    request: ProductRequest | None = None


_ALLOWED_PRODUCT_FIELDS = frozenset(
    {"name", "product_code", "category_id", "selling_price", "cost_price"}
)
_CATALOG_RESOURCE_TYPES = frozenset({"product", "variant"})


def _normalize_money(value, field_name):
    """Return exact DECIMAL(14,3)-compatible money; binary floats are rejected."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{field_name} must be an exact decimal value.")

    if isinstance(value, Decimal):
        amount = value
    elif type(value) is int:
        amount = Decimal(value)
    elif isinstance(value, str) and value.strip():
        decimal_text = value.strip()
        if _DECIMAL_TEXT.fullmatch(decimal_text) is None:
            raise ValueError(f"{field_name} must be a valid decimal value.")
        try:
            amount = Decimal(decimal_text)
        except InvalidOperation:
            raise ValueError(f"{field_name} must be a valid decimal value.") from None
    else:
        raise ValueError(f"{field_name} must be a valid decimal value.")

    if not amount.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal value.")
    if amount < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    if amount > _MONEY_MAX:
        raise ValueError(f"{field_name} must fit DECIMAL(14,3).")

    try:
        normalized = amount.quantize(_MONEY_QUANTUM)
    except InvalidOperation:
        raise ValueError(f"{field_name} must fit DECIMAL(14,3).") from None
    if normalized != amount:
        raise ValueError(f"{field_name} may have at most 3 decimal places.")
    return normalized


def _validate_product_text(value, field_name, max_length):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters.")
    return normalized


def _validate_category_context(
    business_id, category_id, category, *, require_active=True
):
    if not is_positive_integer(category_id):
        raise ValueError("category_id must be a positive integer.")
    if type(category) is not ProductCategoryContext:
        raise ValueError("A trusted category context is required.")
    if not is_positive_integer(category.category_id):
        raise ValueError("The trusted category has an invalid category_id.")
    if category.category_id != category_id:
        raise ValueError("The trusted category does not match category_id.")
    if not resources_belong_to_business(business_id, category.scope):
        raise ValueError("category_id must belong to the same business as the product.")
    if type(category.is_active) is not bool:
        raise ValueError("The trusted category has an invalid is_active value.")
    if require_active and not category.is_active:
        raise ValueError("An inactive category cannot be assigned to a product.")


def _validated_catalog_entry(entry):
    if type(entry) is not CatalogCodeEntry:
        raise ValueError("catalog_codes must contain trusted CatalogCodeEntry values.")
    if not is_positive_integer(entry.business_id):
        raise ValueError("A catalog code has an invalid business_id.")
    if not isinstance(entry.code, str) or not entry.code.strip():
        raise ValueError("A catalog code entry must have a non-empty code.")
    if (
        not isinstance(entry.resource_type, str)
        or entry.resource_type not in _CATALOG_RESOURCE_TYPES
    ):
        raise ValueError("A catalog code has an invalid resource_type.")
    if not is_positive_integer(entry.resource_id):
        raise ValueError("A catalog code has an invalid resource_id.")
    return entry


def ensure_catalog_code_available(
    business_id,
    code,
    catalog_codes,
    *,
    exclude_resource_type=None,
    exclude_resource_id=None,
):
    """Reject a code already used by any product or variant in this business.

    ``catalog_codes`` must come from the shared server-loaded registry. Codes
    from other businesses are allowed. An existing product can be excluded
    during a modification by passing its resource type and ID.
    """
    if not is_positive_integer(business_id):
        raise ValueError("business_id must be a positive integer.")
    normalized_code = _validate_product_text(
        code, "product_code", PRODUCT_CODE_MAX_LENGTH
    )
    if isinstance(catalog_codes, (str, bytes, Mapping)) or not isinstance(
        catalog_codes, Iterable
    ):
        raise ValueError("catalog_codes must be an iterable of trusted entries.")
    if (exclude_resource_type is None) != (exclude_resource_id is None):
        raise ValueError("Both excluded resource type and ID are required.")
    if exclude_resource_type is not None and (
        exclude_resource_type not in _CATALOG_RESOURCE_TYPES
        or not is_positive_integer(exclude_resource_id)
    ):
        raise ValueError("The excluded catalog resource is invalid.")

    # MySQL's usual text collations compare codes without case sensitivity.
    # Apply the same conservative comparison in pure rules so ABC and abc do
    # not become separate catalog identifiers.
    normalized_key = normalized_code.casefold()
    for raw_entry in catalog_codes:
        entry = _validated_catalog_entry(raw_entry)
        if (
            entry.resource_type == exclude_resource_type
            and entry.resource_id == exclude_resource_id
        ):
            continue
        if (
            entry.business_id == business_id
            and entry.code.strip().casefold() == normalized_key
        ):
            raise ValueError("product_code is already used in this business catalog.")
    return normalized_code


def validate_product_data(
    *,
    business_id,
    name,
    product_code,
    category_id,
    selling_price,
    category,
    cost_price=_DEFAULT_COST_PRICE,
    catalog_codes=(),
    exclude_resource_type=None,
    exclude_resource_id=None,
    _require_active_category=True,
):
    """Validate required fields and return normalized shared-catalog metadata.

    The category context and catalog registry must be loaded by the caller from
    trusted server-side data; request-supplied business IDs and scopes are not
    trusted here.
    """
    if not is_positive_integer(business_id):
        raise ValueError("business_id must be a positive integer.")
    if type(_require_active_category) is not bool:
        raise ValueError("_require_active_category must be a boolean.")
    _validate_category_context(
        business_id,
        category_id,
        category,
        require_active=_require_active_category,
    )
    normalized_name = _validate_product_text(
        name, "name", PRODUCT_NAME_MAX_LENGTH
    )
    normalized_code = ensure_catalog_code_available(
        business_id,
        product_code,
        catalog_codes,
        exclude_resource_type=exclude_resource_type,
        exclude_resource_id=exclude_resource_id,
    )
    normalized_selling_price = _normalize_money(selling_price, "selling_price")
    normalized_cost_price = _normalize_money(cost_price, "cost_price")
    return ProductData(
        name=normalized_name,
        product_code=normalized_code,
        category_id=category_id,
        selling_price=normalized_selling_price,
        cost_price=normalized_cost_price,
    )


def _as_action(action):
    try:
        return action if isinstance(action, ProductAction) else ProductAction(action)
    except (TypeError, ValueError):
        raise ValueError("Unknown product catalog action.") from None


def _validate_product_scope(product_id, product_scope):
    if not is_positive_integer(product_id):
        raise ValueError("product_id must be a positive integer.")
    if (
        type(product_scope) is not ResourceScope
        or not is_positive_integer(product_scope.business_id)
        or product_scope.branch_id is not None
    ):
        raise ValueError("A trusted business-wide product scope is required.")


def require_product_catalog_access(
    principal,
    action,
    *,
    product_id=None,
    product_scope=None,
    is_request=False,
):
    """Authorize an owner catalog change or return an employee's pending request.

    Owners use the existing ``product_catalog:manage`` grant. Employees must
    pass ``is_request=True`` and are routed through the established product
    request submission rule. No role receives a permanent-delete operation.
    """
    action = _as_action(action)
    if action is ProductAction.DELETE:
        raise ValueError("Products cannot be permanently deleted.")
    if type(is_request) is not bool:
        raise ValueError("is_request must be a boolean.")
    if not is_trusted_principal(principal):
        raise PermissionError("You are not allowed to manage the product catalog.")

    if action is ProductAction.CREATE:
        if product_id is not None or product_scope is not None:
            raise ValueError("Product creation cannot use an existing product scope.")
        scope = ResourceScope(principal.business_id, None)
    else:
        _validate_product_scope(product_id, product_scope)
        scope = product_scope

    if is_request:
        if scope.business_id != principal.business_id:
            raise PermissionError("Resource not found or access denied.")
        change_type = (
            ProductChangeType.ADD_PRODUCT
            if action is ProductAction.CREATE
            else ProductChangeType.MODIFY_PRODUCT
        )
        return validate_request_submission(
            principal,
            change_type,
            is_shared_catalog=True,
        )

    require_resource_access(principal, PRODUCT_CATALOG_MANAGE, scope)
    return None


def validate_product_creation(
    principal,
    *,
    name,
    product_code,
    category_id,
    selling_price,
    category,
    cost_price=_DEFAULT_COST_PRICE,
    catalog_codes=(),
    is_request=False,
):
    """Validate a product creation proposal without persisting an incomplete row."""
    request = require_product_catalog_access(
        principal,
        ProductAction.CREATE,
        is_request=is_request,
    )
    data = validate_product_data(
        business_id=principal.business_id,
        name=name,
        product_code=product_code,
        category_id=category_id,
        selling_price=selling_price,
        cost_price=cost_price,
        category=category,
        catalog_codes=catalog_codes,
    )
    return ProductProposal(data=data, request=request)


def _read_current_product_data(value):
    if type(value) is not ProductData:
        raise ValueError("A validated current ProductData value is required.")
    return value


def validate_product_modification(
    principal,
    *,
    product_id,
    product_scope,
    current_data,
    changes,
    category,
    catalog_codes=(),
    is_request=False,
):
    """Validate a partial metadata update and leave the loaded product unchanged."""
    request = require_product_catalog_access(
        principal,
        ProductAction.MODIFY,
        product_id=product_id,
        product_scope=product_scope,
        is_request=is_request,
    )
    current = _read_current_product_data(current_data)
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("At least one product field change is required.")
    unknown_fields = set(changes) - _ALLOWED_PRODUCT_FIELDS
    if unknown_fields:
        raise ValueError("Product changes contain unsupported fields.")

    proposed = {
        "name": current.name,
        "product_code": current.product_code,
        "category_id": current.category_id,
        "selling_price": current.selling_price,
        "cost_price": current.cost_price,
    }
    proposed.update(changes)
    data = validate_product_data(
        business_id=product_scope.business_id,
        name=proposed["name"],
        product_code=proposed["product_code"],
        category_id=proposed["category_id"],
        selling_price=proposed["selling_price"],
        cost_price=proposed["cost_price"],
        category=category,
        catalog_codes=catalog_codes,
        exclude_resource_type="product",
        exclude_resource_id=product_id,
        _require_active_category=(proposed["category_id"] != current.category_id),
    )
    return ProductProposal(data=data, request=request)


def validate_product_status_change(
    principal,
    *,
    product_id,
    product_scope,
    is_active,
    is_request=False,
):
    """Validate an activation toggle without touching quantities or history."""
    if type(is_active) is not bool:
        raise ValueError("is_active must be a boolean.")
    action = ProductAction.ACTIVATE if is_active else ProductAction.DEACTIVATE
    request = require_product_catalog_access(
        principal,
        action,
        product_id=product_id,
        product_scope=product_scope,
        is_request=is_request,
    )
    return ProductStatusProposal(is_active=is_active, request=request)


def validate_product_activation(
    principal, *, product_id, product_scope, is_request=False
):
    """Validate an activation proposal (or employee change request)."""
    return validate_product_status_change(
        principal,
        product_id=product_id,
        product_scope=product_scope,
        is_active=True,
        is_request=is_request,
    )


def validate_product_deactivation(
    principal, *, product_id, product_scope, is_request=False
):
    """Validate deactivation while preserving product and transaction history."""
    return validate_product_status_change(
        principal,
        product_id=product_id,
        product_scope=product_scope,
        is_active=False,
        is_request=is_request,
    )


__all__ = [
    "CatalogCodeEntry",
    "PRODUCT_CODE_MAX_LENGTH",
    "PRODUCT_NAME_MAX_LENGTH",
    "ProductAction",
    "ProductCategoryContext",
    "ProductData",
    "ProductProposal",
    "ProductStatusProposal",
    "ensure_catalog_code_available",
    "require_product_catalog_access",
    "validate_product_activation",
    "validate_product_creation",
    "validate_product_data",
    "validate_product_deactivation",
    "validate_product_modification",
    "validate_product_status_change",
]
