"""Pure validation rules for customer returns.

These helpers validate return submissions and inspection quantities only. They
do not persist return requests, connect to a database, or change stock. Callers
must load the principal, sale, branch, and prior returns from trusted
server-side data. A later MySQL transaction must save the return state and any
inspection-stock movement atomically.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum

from permissions import (
    RETURNS_INSPECT,
    RETURNS_RECORD,
    AuthenticatedPrincipal,
    ResourceScope,
    has_permission,
)
from sale_cancellation_rules import Sale, SaleStatus


class ReturnStatus(str, Enum):
    """Statuses relevant to return limits and inspection authorization."""

    PENDING = "Pending"
    VERIFIED = "Verified"
    PARTIALLY_INSPECTED = "Partially Inspected"
    PROCESSED = "Processed"
    REJECTED = "Rejected"


@dataclass(frozen=True)
class ReturnRequest:
    """In-memory return data; this value is not a persisted database record."""

    business_id: int
    branch_id: int
    reason: str
    items: list[Mapping[str, int]] | tuple[Mapping[str, int], ...]
    original_sale_id: int | None = None
    status: ReturnStatus = ReturnStatus.PENDING


def _require_positive_integer(value, field_name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _as_sale_status(status):
    try:
        return status if isinstance(status, SaleStatus) else SaleStatus(status)
    except (TypeError, ValueError):
        raise ValueError("Unknown sale status.") from None


def _as_return_status(status):
    try:
        return status if isinstance(status, ReturnStatus) else ReturnStatus(status)
    except (TypeError, ValueError):
        raise ValueError("Unknown return status.") from None


def validate_return_items(items, *, allow_empty=False):
    """Validate and copy item lines with positive variant IDs and quantities."""
    if not isinstance(items, (list, tuple)):
        raise ValueError("Return items must be a list or tuple.")
    if not items and not allow_empty:
        raise ValueError("A return must contain at least one item.")

    validated_items = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each return item must be a mapping.")
        if "product_variant_id" not in item:
            raise ValueError("product_variant_id is required.")
        if "quantity" not in item:
            raise ValueError("quantity is required.")

        variant_id = item["product_variant_id"]
        quantity = item["quantity"]
        _require_positive_integer(variant_id, "product_variant_id")
        _require_positive_integer(quantity, "quantity")
        validated_items.append(
            {"product_variant_id": variant_id, "quantity": quantity}
        )

    return validated_items


def _quantities_by_variant(items):
    quantities = {}
    for item in items:
        variant_id = item["product_variant_id"]
        quantities[variant_id] = quantities.get(variant_id, 0) + item["quantity"]
    return quantities


def _validated_sale_items(items):
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError("A sale must contain at least one item.")
    validated_items = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each sale item must be a mapping.")
        if "product_variant_id" not in item:
            raise ValueError("product_variant_id is required.")
        if "quantity" not in item:
            raise ValueError("quantity is required.")
        variant_id = item["product_variant_id"]
        quantity = item["quantity"]
        _require_positive_integer(variant_id, "product_variant_id")
        _require_positive_integer(quantity, "quantity")
        validated_items.append(
            {"product_variant_id": variant_id, "quantity": quantity}
        )
    return validated_items


def _validate_sale(sale):
    if not isinstance(sale, Sale):
        raise ValueError("A trusted original sale is required.")
    _require_positive_integer(sale.sale_id, "sale_id")
    _require_positive_integer(sale.business_id, "business_id")
    _require_positive_integer(sale.branch_id, "branch_id")
    _as_sale_status(sale.status)
    _validated_sale_items(sale.items)
    if type(sale.has_processed_returns) is not bool:
        raise ValueError("has_processed_returns must be a boolean.")


def _validate_return_request(return_request):
    if not isinstance(return_request, ReturnRequest):
        raise ValueError("A trusted return request is required.")
    _require_positive_integer(return_request.business_id, "business_id")
    _require_positive_integer(return_request.branch_id, "branch_id")
    if return_request.original_sale_id is not None:
        _require_positive_integer(return_request.original_sale_id, "sale_id")
    if not isinstance(return_request.reason, str) or not return_request.reason.strip():
        raise ValueError("A return reason is required.")
    validate_return_items(return_request.items)
    _as_return_status(return_request.status)


def _return_scope(return_request):
    _validate_return_request(return_request)
    return ResourceScope(
        business_id=return_request.business_id,
        branch_id=return_request.branch_id,
    )


def _require_permission(principal, permission, resource, action):
    if not isinstance(principal, AuthenticatedPrincipal) or not has_permission(
        principal, permission, resource
    ):
        raise PermissionError(f"You are not allowed to {action} this return.")


def _validate_return_collection(existing_returns):
    if existing_returns is None:
        return ()
    if not isinstance(existing_returns, (list, tuple)):
        raise ValueError("Existing returns must be a list or tuple.")
    if any(not isinstance(item, ReturnRequest) for item in existing_returns):
        raise ValueError("Each existing return must be a trusted return request.")
    return existing_returns


def calculate_cumulative_return_quantities(sale, existing_returns=None):
    """Sum active return requests for a sale, including pending inspections.

    Pending, verified, partially inspected, and processed requests reserve
    their full requested quantities. Rejected requests do not reserve any.
    Returns for other sales are ignored.
    """
    _validate_sale(sale)
    returns = _validate_return_collection(existing_returns)
    sale_quantities = _quantities_by_variant(_validated_sale_items(sale.items))
    cumulative = {}

    for return_request in returns:
        if return_request.original_sale_id != sale.sale_id:
            continue
        _validate_return_request(return_request)
        if (
            return_request.business_id != sale.business_id
            or return_request.branch_id != sale.branch_id
        ):
            raise ValueError("An existing return does not match this sale's scope.")

        status = _as_return_status(return_request.status)
        if status is ReturnStatus.REJECTED:
            continue

        for variant_id, quantity in _quantities_by_variant(
            validate_return_items(return_request.items)
        ).items():
            if variant_id not in sale_quantities:
                raise ValueError("An existing return contains an item not sold.")
            cumulative[variant_id] = cumulative.get(variant_id, 0) + quantity

    for variant_id, quantity in cumulative.items():
        if quantity > sale_quantities[variant_id]:
            raise ValueError("Cumulative return quantity cannot exceed the quantity sold.")

    return cumulative


def validate_return_submission(
    principal,
    items,
    reason,
    *,
    sale=None,
    branch_id=None,
    existing_returns=None,
):
    """Validate and return a pending return request.

    For linked returns, the server-loaded sale supplies business and branch
    scope. For unlinked returns, ``branch_id`` must be a trusted server-loaded
    branch selected for this operation; the business comes from the trusted
    principal. Client-supplied identity, role, permissions, and scope must not
    be used to build these values.
    """
    if not isinstance(principal, AuthenticatedPrincipal):
        raise PermissionError("A trusted user is required to record a return.")
    _require_positive_integer(principal.business_id, "business_id")

    if sale is not None:
        _validate_sale(sale)
        if branch_id is not None:
            _require_positive_integer(branch_id, "branch_id")
            if branch_id != sale.branch_id:
                raise ValueError("The selected branch does not match the original sale.")
        business_id = sale.business_id
        resource_branch_id = sale.branch_id
        original_sale_id = sale.sale_id
    else:
        _require_positive_integer(branch_id, "branch_id")
        business_id = principal.business_id
        resource_branch_id = branch_id
        original_sale_id = None

    resource = ResourceScope(
        business_id=business_id,
        branch_id=resource_branch_id,
    )
    _require_permission(principal, RETURNS_RECORD, resource, "record")

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A return reason is required.")
    validated_items = validate_return_items(items)

    if sale is not None:
        sale_status = _as_sale_status(sale.status)
        if sale_status is SaleStatus.CANCELLED:
            raise ValueError("A return cannot be linked to a cancelled sale.")
        if sale_status is not SaleStatus.COMPLETED:
            raise ValueError("Only completed sales can have linked returns.")

        sold_quantities = _quantities_by_variant(_validated_sale_items(sale.items))
        requested_quantities = _quantities_by_variant(validated_items)
        previously_returned = calculate_cumulative_return_quantities(
            sale, existing_returns
        )
        for variant_id, quantity in requested_quantities.items():
            if variant_id not in sold_quantities:
                raise ValueError("The return contains an item not sold in this sale.")
            if previously_returned.get(variant_id, 0) + quantity > sold_quantities[variant_id]:
                raise ValueError(
                    "Cumulative return quantity cannot exceed the quantity sold."
                )
    elif existing_returns:
        raise ValueError("Existing sale returns require an original sale.")

    return ReturnRequest(
        business_id=business_id,
        branch_id=resource_branch_id,
        reason=reason.strip(),
        items=validated_items,
        original_sale_id=original_sale_id,
    )


def validate_unlinked_return_verification(principal, return_request):
    """Authorize an Owner or in-scope Branch Manager to verify an unlinked return.

    The returned value represents the verified state for a caller to persist;
    this function itself changes no data or inventory.
    """
    resource = _return_scope(return_request)
    if return_request.original_sale_id is not None:
        raise ValueError("Only unlinked returns require this verification.")
    if _as_return_status(return_request.status) is not ReturnStatus.PENDING:
        raise ValueError("Only pending unlinked returns can be verified.")
    _require_permission(principal, RETURNS_INSPECT, resource, "verify")
    return replace(return_request, status=ReturnStatus.VERIFIED)


def validate_return_inspection(
    principal,
    return_request,
    accepted_items,
    *,
    previously_inspected_items=None,
):
    """Validate quantities authorized for inspection inventory, without moving stock.

    Only users with ``returns:inspect`` may accept quantities. An unlinked
    return must first have the trusted ``Verified`` status. The result is a
    quantity plan for inspection inventory, never available stock.
    """
    resource = _return_scope(return_request)
    status = _as_return_status(return_request.status)
    if status in {ReturnStatus.REJECTED, ReturnStatus.PROCESSED}:
        raise ValueError("This return cannot be inspected again.")
    if (
        return_request.original_sale_id is None
        and status not in {ReturnStatus.VERIFIED, ReturnStatus.PARTIALLY_INSPECTED}
    ):
        raise ValueError("An unlinked return must be verified before inspection.")

    _require_permission(principal, RETURNS_INSPECT, resource, "inspect")
    current_items = validate_return_items(accepted_items)
    previous_items = validate_return_items(
        () if previously_inspected_items is None else previously_inspected_items,
        allow_empty=True,
    )
    requested = _quantities_by_variant(validate_return_items(return_request.items))
    previously_inspected = _quantities_by_variant(previous_items)
    current = _quantities_by_variant(current_items)

    for variant_id, quantity in previously_inspected.items():
        if variant_id not in requested or quantity > requested[variant_id]:
            raise ValueError("Previously inspected quantity exceeds the return quantity.")
    for variant_id, quantity in current.items():
        if variant_id not in requested:
            raise ValueError("Cannot inspect a product variant that was not returned.")
        if previously_inspected.get(variant_id, 0) + quantity > requested[variant_id]:
            raise ValueError("Inspected quantity cannot exceed the return quantity.")

    return current


def sale_has_processed_returns(sale, existing_returns=None):
    """Return whether a sale has an inspected return that blocks cancellation.

    A partially inspected return counts because return quantities have already
    entered inspection inventory. Callers can use this trusted result to set
    ``Sale.has_processed_returns`` before applying the cancellation rules.
    """
    _validate_sale(sale)
    returns = _validate_return_collection(existing_returns)
    for return_request in returns:
        if return_request.original_sale_id != sale.sale_id:
            continue
        _validate_return_request(return_request)
        if (
            return_request.business_id != sale.business_id
            or return_request.branch_id != sale.branch_id
        ):
            raise ValueError("An existing return does not match this sale's scope.")
        if _as_return_status(return_request.status) in {
            ReturnStatus.PARTIALLY_INSPECTED,
            ReturnStatus.PROCESSED,
        }:
            return True
    return False
