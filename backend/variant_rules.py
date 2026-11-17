"""Pure validation and authorization rules for business product variants.

The caller supplies trusted product/variant scopes and shared catalog-code
entries loaded by the server. These functions return proposals only: they do
not create default variants, persist metadata, or change inventory quantities.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from isolation_rules import require_resource_access
from permissions import (
    PRODUCT_CATALOG_MANAGE,
    ResourceScope,
    is_trusted_principal,
    is_valid_resource_scope,
)
from product_request_rules import ProductChangeType, ProductRequest, validate_request_submission
from product_rules import (
    PRODUCT_CODE_MAX_LENGTH,
    CatalogCodeEntry,
    _normalize_money,
    ensure_catalog_code_available,
)
from request_validation import is_positive_integer


class VariantAction(str, Enum):
    """Supported variant changes. Historical variants are never deleted."""

    CREATE = "create"
    MODIFY = "modify"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


@dataclass(frozen=True)
class ProductContext:
    """A server-loaded product and its business-wide scope."""

    product_id: int
    scope: ResourceScope


@dataclass(frozen=True)
class ProductVariantContext:
    """A server-loaded variant's immutable product association and default flag."""

    variant_id: int
    product_id: int
    scope: ResourceScope
    is_default: bool


@dataclass(frozen=True)
class VariantData:
    """Normalized variant attributes and optional price overrides."""

    product_id: int
    code: str
    size: str | None = None
    color: str | None = None
    selling_price: Decimal | None = None
    cost_price: Decimal | None = None
    is_default: bool = False


@dataclass(frozen=True)
class VariantProposal:
    """An in-memory variant proposal and optional employee request."""

    data: VariantData
    request: ProductRequest | None = None


@dataclass(frozen=True)
class VariantStatusProposal:
    """An activation proposal that keeps variant and transaction history."""

    is_active: bool
    request: ProductRequest | None = None


_ALLOWED_VARIANT_FIELDS = frozenset(
    {"code", "size", "color", "selling_price", "cost_price", "is_default"}
)


def _validate_product_context(product_id, product):
    if not is_positive_integer(product_id):
        raise ValueError("product_id must be a positive integer.")
    if type(product) is not ProductContext:
        raise ValueError("A trusted product context is required.")
    if not is_positive_integer(product.product_id):
        raise ValueError("The trusted product has an invalid product_id.")
    if product.product_id != product_id:
        raise ValueError("The trusted product does not match product_id.")
    if not is_valid_resource_scope(product.scope) or product.scope.branch_id is not None:
        raise ValueError("A trusted business-wide product scope is required.")


def _validate_variant_context(variant_id, variant, product):
    if not is_positive_integer(variant_id):
        raise ValueError("product_variant_id must be a positive integer.")
    if type(variant) is not ProductVariantContext:
        raise ValueError("A trusted product variant context is required.")
    if not is_positive_integer(variant.variant_id):
        raise ValueError("The trusted variant has an invalid product_variant_id.")
    if variant.variant_id != variant_id:
        raise ValueError("The trusted variant does not match product_variant_id.")
    if not is_positive_integer(variant.product_id):
        raise ValueError("The trusted variant has an invalid product_id.")
    if variant.product_id != product.product_id:
        raise ValueError("The variant does not belong to the specified product.")
    if not is_valid_resource_scope(variant.scope) or variant.scope.branch_id is not None:
        raise ValueError("A trusted business-wide variant scope is required.")
    if variant.scope.business_id != product.scope.business_id:
        raise ValueError("The product and variant must belong to the same business.")
    if type(variant.is_default) is not bool:
        raise ValueError("The trusted variant has an invalid is_default value.")


def _authorize(principal, action, product, *, is_request=False):
    if type(is_request) is not bool:
        raise ValueError("is_request must be a boolean.")
    if not is_trusted_principal(principal):
        raise PermissionError("You are not allowed to manage the product catalog.")
    if product.scope.business_id != principal.business_id:
        raise PermissionError("Resource not found or access denied.")

    if is_request:
        change_type = (
            ProductChangeType.ADD_VARIANT
            if action is VariantAction.CREATE
            else ProductChangeType.MODIFY_VARIANT
        )
        return validate_request_submission(
            principal, change_type, is_shared_catalog=True
        )

    require_resource_access(principal, PRODUCT_CATALOG_MANAGE, product.scope)
    return None


def _optional_attribute(value, field_name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string when provided.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty when provided.")
    return normalized


def _normalize_variant_data(
    *, product_id, code, size, color, selling_price, cost_price, is_default
):
    if type(is_default) is not bool:
        raise ValueError("is_default must be a boolean.")
    if not isinstance(code, str):
        raise ValueError("code must be a string.")
    normalized_code = code.strip()
    if not normalized_code:
        raise ValueError("code is required.")
    if len(normalized_code) > PRODUCT_CODE_MAX_LENGTH:
        raise ValueError(f"code must be at most {PRODUCT_CODE_MAX_LENGTH} characters.")

    normalized_size = _optional_attribute(size, "size")
    normalized_color = _optional_attribute(color, "color")
    if is_default and (normalized_size is not None or normalized_color is not None):
        raise ValueError("A default variant cannot have size or color attributes.")
    if not is_default and normalized_size is None and normalized_color is None:
        raise ValueError("A non-default variant requires size or color.")

    return VariantData(
        product_id=product_id,
        code=normalized_code,
        size=normalized_size,
        color=normalized_color,
        selling_price=(
            None if selling_price is None else _normalize_money(selling_price, "selling_price")
        ),
        cost_price=(
            None if cost_price is None else _normalize_money(cost_price, "cost_price")
        ),
        is_default=is_default,
    )


def _variant_records(variants):
    if isinstance(variants, (str, bytes, Mapping)) or not isinstance(variants, Iterable):
        raise ValueError("existing_variants must be an iterable of trusted contexts.")
    for variant in variants:
        if type(variant) is not ProductVariantContext:
            raise ValueError(
                "existing_variants must contain trusted ProductVariantContext values."
            )
        if not is_positive_integer(variant.variant_id):
            raise ValueError("An existing variant has an invalid product_variant_id.")
        if not is_positive_integer(variant.product_id):
            raise ValueError("An existing variant has an invalid product_id.")
        if not is_valid_resource_scope(variant.scope) or variant.scope.branch_id is not None:
            raise ValueError("An existing variant has an invalid business scope.")
        if type(variant.is_default) is not bool:
            raise ValueError("An existing variant has an invalid is_default value.")
        yield variant


def _ensure_default_variant_available(
    product, existing_variants, *, exclude_variant_id=None
):
    for variant in _variant_records(existing_variants):
        if variant.variant_id == exclude_variant_id:
            continue
        if (
            variant.scope.business_id == product.scope.business_id
            and variant.product_id == product.product_id
            and variant.is_default
        ):
            raise ValueError("This product already has a default variant.")


def _normalize_code_with_registry(
    business_id, code, catalog_codes, *, exclude_variant_id=None
):
    return ensure_catalog_code_available(
        business_id,
        code,
        catalog_codes,
        exclude_resource_type="variant" if exclude_variant_id is not None else None,
        exclude_resource_id=exclude_variant_id,
    )


def validate_variant_creation(
    principal,
    *,
    product_id,
    product,
    code,
    size=None,
    color=None,
    selling_price=None,
    cost_price=None,
    is_default=False,
    catalog_codes=(),
    existing_variants=(),
    is_request=False,
):
    """Validate a new variant, including simple products' default variant."""
    _validate_product_context(product_id, product)
    request = _authorize(principal, VariantAction.CREATE, product, is_request=is_request)
    data = _normalize_variant_data(
        product_id=product_id,
        code=code,
        size=size,
        color=color,
        selling_price=selling_price,
        cost_price=cost_price,
        is_default=is_default,
    )
    data = VariantData(
        **{
            **data.__dict__,
            "code": _normalize_code_with_registry(
                product.scope.business_id, data.code, catalog_codes
            ),
        }
    )
    if data.is_default:
        _ensure_default_variant_available(product, existing_variants)
    else:
        # Still validate the server-loaded list so malformed ownership data is
        # never silently accepted by this entry point.
        tuple(_variant_records(existing_variants))
    return VariantProposal(data=data, request=request)


def _read_current_variant_data(value, product_id, variant):
    if type(value) is not VariantData:
        raise ValueError("A validated current VariantData value is required.")
    if not is_positive_integer(value.product_id) or value.product_id != product_id:
        raise ValueError("The current variant does not belong to the specified product.")
    if type(value.is_default) is not bool or value.is_default != variant.is_default:
        raise ValueError("The current variant data does not match its trusted context.")
    return value


def validate_variant_modification(
    principal,
    *,
    product_id,
    product,
    product_variant_id,
    variant,
    current_data,
    changes,
    catalog_codes=(),
    existing_variants=(),
    is_request=False,
):
    """Validate a partial variant update while keeping its product association."""
    _validate_product_context(product_id, product)
    _validate_variant_context(product_variant_id, variant, product)
    request = _authorize(principal, VariantAction.MODIFY, product, is_request=is_request)
    current = _read_current_variant_data(current_data, product_id, variant)
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("At least one variant field change is required.")
    if set(changes) - _ALLOWED_VARIANT_FIELDS:
        raise ValueError("Variant changes contain unsupported fields.")

    proposed = {
        "code": current.code,
        "size": current.size,
        "color": current.color,
        "selling_price": current.selling_price,
        "cost_price": current.cost_price,
        "is_default": current.is_default,
    }
    proposed.update(changes)
    data = _normalize_variant_data(product_id=product_id, **proposed)
    data = VariantData(
        **{
            **data.__dict__,
            "code": _normalize_code_with_registry(
                product.scope.business_id,
                data.code,
                catalog_codes,
                exclude_variant_id=product_variant_id,
            ),
        }
    )
    if data.is_default:
        _ensure_default_variant_available(
            product, existing_variants, exclude_variant_id=product_variant_id
        )
    else:
        tuple(_variant_records(existing_variants))
    return VariantProposal(data=data, request=request)


def validate_variant_status_change(
    principal,
    *,
    product_id,
    product,
    product_variant_id,
    variant,
    is_active,
    is_request=False,
):
    """Validate a safe activation toggle without touching stock or history."""
    if type(is_active) is not bool:
        raise ValueError("is_active must be a boolean.")
    _validate_product_context(product_id, product)
    _validate_variant_context(product_variant_id, variant, product)
    action = VariantAction.ACTIVATE if is_active else VariantAction.DEACTIVATE
    request = _authorize(principal, action, product, is_request=is_request)
    return VariantStatusProposal(is_active=is_active, request=request)


def validate_variant_activation(
    principal,
    *,
    product_id,
    product,
    product_variant_id,
    variant,
    is_request=False,
):
    """Validate variant activation or submit an employee request."""
    return validate_variant_status_change(
        principal,
        product_id=product_id,
        product=product,
        product_variant_id=product_variant_id,
        variant=variant,
        is_active=True,
        is_request=is_request,
    )


def validate_variant_deactivation(
    principal,
    *,
    product_id,
    product,
    product_variant_id,
    variant,
    is_request=False,
):
    """Validate deactivation while preserving historical variant references."""
    return validate_variant_status_change(
        principal,
        product_id=product_id,
        product=product,
        product_variant_id=product_variant_id,
        variant=variant,
        is_active=False,
        is_request=is_request,
    )


__all__ = [
    "CatalogCodeEntry",
    "ProductContext",
    "ProductVariantContext",
    "VariantAction",
    "VariantData",
    "VariantProposal",
    "VariantStatusProposal",
    "validate_variant_activation",
    "validate_variant_creation",
    "validate_variant_deactivation",
    "validate_variant_modification",
    "validate_variant_status_change",
]
