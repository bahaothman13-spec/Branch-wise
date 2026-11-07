"""Pure preparation and validation rules for point-of-sale transactions.

The caller supplies a principal, branch, product and variant contexts, and
inventory records loaded or verified by trusted server code. These rules do
not create sales, allocate references, persist idempotency keys, or update
inventory. The responsible user ID must come from the same verified identity
as ``principal``; the principal type currently has no user ID to cross-check.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from isolation_rules import require_resource_access
from inventory_movement_rules import build_sale_movements
from permissions import (
    SALES_RECORD,
    ResourceScope,
    is_trusted_principal,
    is_valid_resource_scope,
)
from product_rules import _normalize_money
from request_validation import is_positive_integer
from sale_cancellation_rules import Sale, SaleStatus
from stock_rules import (
    get_available_quantity,
    validate_requested_quantity,
    validate_stock_availability,
)


_MONEY_MAX_MILLIUNITS = 9_999_999_999_999_999


@dataclass(frozen=True)
class SaleProductContext:
    """Trusted business-wide product details needed to price a sale."""

    product_id: int
    scope: ResourceScope
    selling_price: Decimal
    is_active: bool


@dataclass(frozen=True)
class SaleVariantContext:
    """Trusted variant details; a ``None`` price uses its product price."""

    variant_id: int
    product_id: int
    scope: ResourceScope
    selling_price: Decimal | None
    is_active: bool


@dataclass(frozen=True)
class SaleLineItem:
    """A cart line copied with trusted price snapshots and exact amounts."""

    product_id: int
    product_variant_id: int
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


@dataclass(frozen=True)
class ProposedSaleDeduction:
    """Aggregated quantity to deduct for one branch and product variant."""

    product_variant_id: int
    quantity: int


@dataclass(frozen=True)
class SaleProposal:
    """Validated sale data prepared for a later persistence transaction."""

    business_id: int
    branch_id: int
    responsible_user_id: int
    items: tuple[SaleLineItem, ...]
    total_amount: Decimal
    inventory_deductions: tuple[ProposedSaleDeduction, ...]
    # Filled by the MySQL integration layer; this module never fabricates them.
    sale_reference: None = None
    request_idempotency_key: None = None


def _validate_cart(items):
    if not isinstance(items, (list, tuple)):
        raise ValueError("Sale items must be a list or tuple.")
    if not items:
        raise ValueError("A sale must contain at least one item.")

    validated = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each sale item must be a mapping.")
        for field_name in ("product_id", "product_variant_id", "quantity"):
            if field_name not in item:
                raise ValueError(f"{field_name} is required.")

        product_id = item["product_id"]
        variant_id = item["product_variant_id"]
        quantity = item["quantity"]
        if not is_positive_integer(product_id):
            raise ValueError("product_id must be a positive integer.")
        if not is_positive_integer(variant_id):
            raise ValueError("product_variant_id must be a positive integer.")
        validate_requested_quantity(quantity)
        validated.append(
            {
                "product_id": product_id,
                "product_variant_id": variant_id,
                "quantity": quantity,
            }
        )
    return validated


def _validate_product_context(product_id, product):
    if type(product) is not SaleProductContext:
        raise ValueError("A trusted product context is required.")
    if not is_positive_integer(product.product_id) or product.product_id != product_id:
        raise ValueError("The trusted product does not match product_id.")
    if (
        not is_valid_resource_scope(product.scope)
        or product.scope.branch_id is not None
    ):
        raise ValueError("A trusted business-wide product scope is required.")
    if type(product.is_active) is not bool:
        raise ValueError("The trusted product has an invalid is_active value.")


def _validate_variant_context(variant_id, variant, product_id):
    if type(variant) is not SaleVariantContext:
        raise ValueError("A trusted product variant context is required.")
    if not is_positive_integer(variant.variant_id) or variant.variant_id != variant_id:
        raise ValueError("The trusted variant does not match product_variant_id.")
    if not is_positive_integer(variant.product_id):
        raise ValueError("The trusted variant has an invalid product_id.")
    if variant.product_id != product_id:
        raise ValueError("The product variant does not belong to product_id.")
    if (
        not is_valid_resource_scope(variant.scope)
        or variant.scope.branch_id is not None
    ):
        raise ValueError("A trusted business-wide variant scope is required.")
    if type(variant.is_active) is not bool:
        raise ValueError("The trusted variant has an invalid is_active value.")


def _as_milliunits(amount):
    """Convert normalized three-decimal money to an exact integer amount."""
    return int(amount * 1000)


def _from_milliunits(amount):
    whole, fraction = divmod(amount, 1000)
    return Decimal(f"{whole}.{fraction:03d}")


def _checked_amount(milliunits, field_name):
    if milliunits < 0 or milliunits > _MONEY_MAX_MILLIUNITS:
        raise ValueError(f"{field_name} must fit DECIMAL(16,3).")
    return _from_milliunits(milliunits)


def prepare_sale(
    principal,
    *,
    actor_id,
    branch_scope,
    items,
    products,
    variants,
    inventory_records,
):
    """Validate a POS cart and return a sale proposal without side effects.

    ``products`` maps product IDs to trusted ``SaleProductContext`` records;
    ``variants`` maps variant IDs to trusted ``SaleVariantContext`` records.
    ``inventory_records`` maps ``(branch_id, variant_id)`` to trusted stock
    records. Any optional client price fields on cart lines are ignored; only
    the server-provided catalog prices are used.
    """
    cart = _validate_cart(items)
    if not is_positive_integer(actor_id):
        raise ValueError("actor_id must be a positive integer.")
    if not is_valid_resource_scope(branch_scope) or not is_positive_integer(
        branch_scope.branch_id
    ):
        raise ValueError("A trusted branch resource context is required.")
    if not is_trusted_principal(principal):
        raise PermissionError("Resource not found or access denied.")
    if not isinstance(products, Mapping):
        raise ValueError("products must map product IDs to trusted contexts.")
    if not isinstance(variants, Mapping):
        raise ValueError("variants must map variant IDs to trusted contexts.")
    if not isinstance(inventory_records, Mapping):
        raise ValueError("inventory_records must map branch and variant IDs to records.")

    prepared_lines = []
    quantities_by_variant = {}

    for item in cart:
        product_id = item["product_id"]
        variant_id = item["product_variant_id"]
        product = products.get(product_id)
        variant = variants.get(variant_id)
        if product is None:
            raise ValueError("A trusted product context is required.")
        if variant is None:
            raise ValueError("A trusted product variant context is required.")
        _validate_product_context(product_id, product)
        _validate_variant_context(variant_id, variant, product_id)

        # Authorization checks the branch plus both business-owned catalog
        # resources. Client IDs, roles, and grants are never used as context.
        require_resource_access(
            principal,
            SALES_RECORD,
            branch_scope,
            related_scopes=(product.scope, variant.scope),
        )
        if not product.is_active:
            raise ValueError("Inactive products cannot be sold.")
        if not variant.is_active:
            raise ValueError("Inactive product variants cannot be sold.")

        trusted_price = (
            product.selling_price
            if variant.selling_price is None
            else variant.selling_price
        )
        product_price = _normalize_money(product.selling_price, "selling_price")
        unit_price = (
            product_price
            if variant.selling_price is None
            else _normalize_money(trusted_price, "selling_price")
        )
        line_milliunits = _as_milliunits(unit_price) * item["quantity"]
        subtotal = _checked_amount(line_milliunits, "Sale line subtotal")
        prepared_lines.append(
            SaleLineItem(
                product_id=product_id,
                product_variant_id=variant_id,
                quantity=item["quantity"],
                unit_price=unit_price,
                subtotal=subtotal,
            )
        )
        quantities_by_variant[variant_id] = (
            quantities_by_variant.get(variant_id, 0) + item["quantity"]
        )
    # Validate each aggregated variant quantity once, so duplicate cart lines
    # cannot independently pass against the same available balance.
    deductions = []
    for variant_id, quantity in quantities_by_variant.items():
        inventory_record = inventory_records.get((branch_scope.branch_id, variant_id))
        available = get_available_quantity(
            inventory_record, branch_scope.branch_id, variant_id
        )
        validate_stock_availability(available, quantity)
        deductions.append(
            ProposedSaleDeduction(
                product_variant_id=variant_id,
                quantity=quantity,
            )
        )

    total_milliunits = sum(_as_milliunits(line.subtotal) for line in prepared_lines)
    total_amount = _checked_amount(total_milliunits, "Sale total")

    return SaleProposal(
        business_id=branch_scope.business_id,
        branch_id=branch_scope.branch_id,
        responsible_user_id=actor_id,
        items=tuple(prepared_lines),
        total_amount=total_amount,
        inventory_deductions=tuple(deductions),
    )


def build_sale_inventory_movements(
    principal,
    *,
    actor_id,
    sale_id,
    proposal,
    branch_scope,
    variant_scopes,
):
    """Delegate deduction construction to inventory movement rules.

    ``sale_id`` must be a real, server-assigned ID supplied by the later
    persistence layer. This helper does not allocate or persist it. Call it
    only once that ID exists; the returned movements remain proposals and do
    not modify inventory.
    """
    if type(proposal) is not SaleProposal:
        raise ValueError("A trusted sale proposal is required.")
    if not is_positive_integer(actor_id) or actor_id != proposal.responsible_user_id:
        raise PermissionError("Resource not found or access denied.")
    if not is_positive_integer(sale_id):
        raise ValueError("sale_id must be a positive integer.")
    if (
        not is_valid_resource_scope(branch_scope)
        or branch_scope.business_id != proposal.business_id
        or branch_scope.branch_id != proposal.branch_id
    ):
        raise ValueError("The branch scope does not match the sale proposal.")

    sale = Sale(
        sale_id=sale_id,
        business_id=proposal.business_id,
        branch_id=proposal.branch_id,
        status=SaleStatus.COMPLETED,
        items=[
            {
                "product_variant_id": deduction.product_variant_id,
                "quantity": deduction.quantity,
            }
            for deduction in proposal.inventory_deductions
        ],
    )
    return build_sale_movements(
        principal,
        actor_id=actor_id,
        sale=sale,
        branch_scope=branch_scope,
        variant_scopes=variant_scopes,
    )


__all__ = [
    "ProposedSaleDeduction",
    "SaleLineItem",
    "SaleProductContext",
    "SaleProposal",
    "SaleVariantContext",
    "build_sale_inventory_movements",
    "prepare_sale",
]
