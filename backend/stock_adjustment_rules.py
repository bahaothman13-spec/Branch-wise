"""Pure business rules for proposed manual stock adjustments.

The caller must build the principal and resource scopes from authenticated
identity and server-loaded records. These helpers validate and describe a
proposed inventory movement; they never update inventory or persist a record.
"""

from dataclasses import dataclass
from enum import Enum

from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    resources_belong_to_business,
)
from permissions import INVENTORY_ADJUST, is_trusted_principal, is_valid_resource_scope
from request_validation import is_positive_integer
from stock_rules import (
    calculate_remaining_quantity,
    get_available_quantity,
    require_branch_variant_stock_access,
    validate_available_quantity,
    validate_requested_quantity,
    validate_stock_availability,
)


class AdjustmentDirection(str, Enum):
    """Supported directions for a manual inventory adjustment."""

    INCREASE = "INCREASE"
    DECREASE = "DECREASE"


@dataclass(frozen=True)
class InventoryMovementDraft:
    """Trusted data to persist later as an append-only inventory movement."""

    actor_id: int
    business_id: int
    branch_id: int
    product_variant_id: int
    direction: AdjustmentDirection
    quantity: int
    reason: str
    adjustment_reference: str | None = None


@dataclass(frozen=True)
class StockAdjustmentResult:
    """A proposed movement and its expected available-stock effect."""

    available_qty_before: int
    quantity_change: int
    expected_available_qty: int
    movement: InventoryMovementDraft


def _as_direction(direction):
    """Normalize a supported direction without accepting arbitrary values."""
    try:
        return direction if isinstance(direction, AdjustmentDirection) else AdjustmentDirection(direction)
    except (TypeError, ValueError):
        raise ValueError("direction must be INCREASE or DECREASE.") from None


def _validate_reason(reason):
    """Return a trimmed reason, rejecting missing or non-text explanations."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a meaningful, non-empty string.")
    return reason.strip()


def validate_adjustment_reference(
    adjustment_reference=None, existing_adjustment_references=None
):
    """Validate a caller-supplied reference and check trusted prior references.

    The reference is never generated here. If a trusted collection of already
    processed references is supplied, a repeated value is rejected. Persistence
    must still enforce uniqueness transactionally when it writes the movement.
    """
    reference = None
    if adjustment_reference is not None:
        if not isinstance(adjustment_reference, str) or not adjustment_reference.strip():
            raise ValueError("adjustment_reference must be a non-empty string.")
        reference = adjustment_reference.strip()

    if existing_adjustment_references is not None:
        if not isinstance(existing_adjustment_references, (tuple, list, set, frozenset)):
            raise ValueError("existing_adjustment_references must be a collection.")
        normalized_existing = set()
        for existing_reference in existing_adjustment_references:
            if not isinstance(existing_reference, str) or not existing_reference.strip():
                raise ValueError("Existing adjustment references must be non-empty strings.")
            normalized_existing.add(existing_reference.strip())
        if reference is not None and reference in normalized_existing:
            raise ValueError("This adjustment reference has already been processed.")

    return reference


def _validate_resource_context(
    principal, branch_scope, product_scope, variant_scope
):
    """Validate trusted ownership scopes and authorize inventory adjustment."""
    if not is_trusted_principal(principal):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if (
        not is_valid_resource_scope(branch_scope)
        or not is_positive_integer(branch_scope.branch_id)
        or not is_valid_resource_scope(product_scope)
        or not is_valid_resource_scope(variant_scope)
    ):
        raise ValueError("Invalid trusted stock adjustment resource context.")

    if not resources_belong_to_business(
        principal.business_id, branch_scope, product_scope, variant_scope
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    # The existing stock access rule enforces inventory-adjust permission and
    # branch access, while the product and variant scopes above enforce ownership.
    require_branch_variant_stock_access(
        principal,
        branch_scope,
        variant_scope,
        required_permission=INVENTORY_ADJUST,
    )


def validate_stock_adjustment(
    principal,
    *,
    actor_id,
    branch_scope,
    product_scope,
    variant_scope,
    product_variant_id,
    inventory_record,
    direction,
    quantity,
    reason,
    adjustment_reference=None,
    existing_adjustment_references=None,
):
    """Validate a manual adjustment and return its expected stock effect.

    ``principal``, all resource scopes, ``actor_id``, ``product_variant_id``,
    ``inventory_record`` and any existing-reference collection must come from
    trusted server-side context. In particular, load the product and variant
    before calling this function; do not construct trusted scopes from request
    fields. A missing inventory record means zero stock; an existing malformed
    record is rejected by :func:`stock_rules.get_available_quantity`.
    """
    _validate_resource_context(principal, branch_scope, product_scope, variant_scope)
    if not is_positive_integer(actor_id):
        raise ValueError("actor_id must be a positive integer.")
    if not is_positive_integer(product_variant_id):
        raise ValueError("product_variant_id must be a positive integer.")

    adjustment_direction = _as_direction(direction)
    requested_quantity = validate_requested_quantity(quantity)
    normalized_reason = _validate_reason(reason)
    reference = validate_adjustment_reference(
        adjustment_reference, existing_adjustment_references
    )

    branch_id = branch_scope.branch_id
    available_before = get_available_quantity(
        inventory_record, branch_id, product_variant_id
    )

    if adjustment_direction is AdjustmentDirection.DECREASE:
        validate_stock_availability(available_before, requested_quantity)
        expected_available = calculate_remaining_quantity(
            available_before, requested_quantity
        )
        signed_change = -requested_quantity
    else:
        available_before = validate_available_quantity(available_before)
        expected_available = available_before + requested_quantity
        signed_change = requested_quantity

    movement = InventoryMovementDraft(
        actor_id=actor_id,
        business_id=branch_scope.business_id,
        branch_id=branch_id,
        product_variant_id=product_variant_id,
        direction=adjustment_direction,
        quantity=requested_quantity,
        reason=normalized_reason,
        adjustment_reference=reference,
    )
    return StockAdjustmentResult(
        available_qty_before=available_before,
        quantity_change=signed_change,
        expected_available_qty=expected_available,
        movement=movement,
    )


__all__ = [
    "AdjustmentDirection",
    "InventoryMovementDraft",
    "StockAdjustmentResult",
    "validate_adjustment_reference",
    "validate_stock_adjustment",
]
