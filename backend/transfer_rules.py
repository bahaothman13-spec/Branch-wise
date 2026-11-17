"""Pure business rules for stock transfers.

These helpers validate transfer data and calculate quantity changes. They do
not read or update inventory, save transfers, or connect to the database.
"""

from collections.abc import Mapping
from enum import Enum


class TransferStatus(str, Enum):
    """Statuses available to a stock transfer."""

    PENDING = "Pending"
    IN_TRANSIT = "In Transit"
    COMPLETED = "Completed"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"


ALLOWED_STATUS_TRANSITIONS = {
    TransferStatus.PENDING: frozenset(
        {
            TransferStatus.IN_TRANSIT,
            TransferStatus.REJECTED,
            TransferStatus.CANCELLED,
        }
    ),
    TransferStatus.IN_TRANSIT: frozenset({TransferStatus.COMPLETED}),
    TransferStatus.COMPLETED: frozenset(),
    TransferStatus.REJECTED: frozenset(),
    TransferStatus.CANCELLED: frozenset(),
}


def _require_positive_integer(value, field_name):
    """Reject booleans and other values that are not positive integers."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def validate_branch_ids(source_branch_id, destination_branch_id):
    """Validate branch IDs and ensure a transfer goes between two branches."""
    _require_positive_integer(source_branch_id, "source_branch_id")
    _require_positive_integer(destination_branch_id, "destination_branch_id")
    if source_branch_id == destination_branch_id:
        raise ValueError("Source and destination branches must be different.")


def validate_transfer_items(items, allow_empty=False):
    """Validate and copy transfer lines with positive IDs and quantities."""
    if not isinstance(items, (list, tuple)):
        raise ValueError("Transfer items must be a list or tuple.")
    if not items and not allow_empty:
        raise ValueError("A transfer must contain at least one item.")

    validated_items = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each transfer item must be a mapping.")
        if "product_variant_id" not in item:
            raise ValueError("product_variant_id is required.")
        if "quantity" not in item:
            raise ValueError("quantity is required.")

        product_variant_id = item["product_variant_id"]
        quantity = item["quantity"]
        _require_positive_integer(product_variant_id, "product_variant_id")
        _require_positive_integer(quantity, "quantity")
        validated_items.append(
            {"product_variant_id": product_variant_id, "quantity": quantity}
        )

    return validated_items


def validate_transfer(source_branch_id, destination_branch_id, items):
    """Validate the branches and item lines for a new transfer."""
    validate_branch_ids(source_branch_id, destination_branch_id)
    return validate_transfer_items(items)


def _quantities_by_variant(items, allow_empty=False):
    """Combine item lines into total quantities per product variant."""
    quantities = {}
    for item in validate_transfer_items(items, allow_empty=allow_empty):
        variant_id = item["product_variant_id"]
        quantities[variant_id] = quantities.get(variant_id, 0) + item["quantity"]
    return quantities


def calculate_dispatch_quantities(items):
    """Return quantities to deduct from source stock when dispatching.

    The transfer should move to In Transit in the same database transaction
    that applies these deductions, so a repeated action cannot deduct twice.
    This function itself does not change stock.
    """
    return _quantities_by_variant(items)


def calculate_receipt_quantities(
    receipt_items, dispatched_items, previously_received_items=None
):
    """Return this receipt's quantities to add to destination stock.

    ``receipt_items`` contains only the quantities confirmed in this receipt.
    Previous receipts are included when checking that cumulative receipt does
    not exceed the dispatched amount. Repeated receipt actions must be guarded
    by a transaction and transfer state in the caller; this function is pure.
    """
    dispatched = _quantities_by_variant(dispatched_items)
    previous_items = [] if previously_received_items is None else previously_received_items
    previous = _quantities_by_variant(previous_items, allow_empty=True)
    current = _quantities_by_variant(receipt_items)

    for variant_id, quantity in previous.items():
        if variant_id not in dispatched or quantity > dispatched[variant_id]:
            raise ValueError("Previously received quantity exceeds dispatched quantity.")

    for variant_id, quantity in current.items():
        if variant_id not in dispatched:
            raise ValueError("Cannot receive a product variant that was not dispatched.")
        already_received = previous.get(variant_id, 0)
        if already_received + quantity > dispatched[variant_id]:
            raise ValueError("Received quantity cannot exceed dispatched quantity.")

    return current


def calculate_quantity_discrepancies(dispatched_items, received_items):
    """Return each product variant's still-missing quantity, if any.

    ``received_items`` represents the cumulative confirmed receipt to date.
    A positive result remains outstanding until a later receipt resolves it.
    """
    dispatched = _quantities_by_variant(dispatched_items)
    received = _quantities_by_variant(received_items, allow_empty=True)

    discrepancies = {}
    for variant_id, quantity in received.items():
        if variant_id not in dispatched:
            raise ValueError("Cannot receive a product variant that was not dispatched.")
        if quantity > dispatched[variant_id]:
            raise ValueError("Received quantity cannot exceed dispatched quantity.")

    for variant_id, quantity in dispatched.items():
        missing_quantity = quantity - received.get(variant_id, 0)
        if missing_quantity:
            discrepancies[variant_id] = missing_quantity

    return discrepancies


def _as_status(status):
    """Return a TransferStatus or raise ValueError for an unknown status."""
    try:
        return status if isinstance(status, TransferStatus) else TransferStatus(status)
    except (TypeError, ValueError):
        raise ValueError("Unknown transfer status.") from None


def validate_status_transition(
    current_status,
    new_status,
    dispatched_items=None,
    received_items=None,
):
    """Validate a status change and require confirmed receipt before completion.

    A transfer may be completed with an outstanding quantity discrepancy.
    The caller should save the status, received quantities, and discrepancy
    together so a repeated confirmation cannot change stock twice.
    """
    current = _as_status(current_status)
    new = _as_status(new_status)

    if new not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise ValueError(f"Cannot change transfer status from {current.value} to {new.value}.")

    if new is TransferStatus.COMPLETED:
        if dispatched_items is None or received_items is None:
            raise ValueError("Received quantities are required to complete a transfer.")
        # Keep the existing rule that confirmation contains at least one item.
        validate_transfer_items(received_items)
        # Validate actual received quantities, but keep any missing amount as
        # a discrepancy instead of blocking completion.
        calculate_quantity_discrepancies(dispatched_items, received_items)
