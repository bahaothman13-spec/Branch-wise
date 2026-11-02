"""Pure validation and quantity calculations for sale cancellation requests.

These rules do not save requests, change sale statuses, or update inventory.
The caller must load the principal, sale, and existing requests from trusted
server-side data. A later database transaction must apply an approval, update
the sale, and restore inventory together to guarantee exactly-once processing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from permissions import (
    SALE_CANCELLATION_APPROVAL,
    SALE_CANCELLATION_REQUEST,
    AuthenticatedPrincipal,
    ResourceScope,
    has_permission,
)


class SaleStatus(str, Enum):
    """Sale statuses needed by cancellation rules."""

    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class SaleCancellationStatus(str, Enum):
    """Statuses for a sale cancellation request."""

    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


ALLOWED_STATUS_TRANSITIONS = {
    SaleCancellationStatus.PENDING: frozenset(
        {SaleCancellationStatus.APPROVED, SaleCancellationStatus.REJECTED}
    ),
    SaleCancellationStatus.APPROVED: frozenset(),
    SaleCancellationStatus.REJECTED: frozenset(),
}

ALLOWED_SALE_STATUS_TRANSITIONS = {
    SaleStatus.COMPLETED: frozenset({SaleStatus.CANCELLED}),
    SaleStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class Sale:
    """Trusted sale data loaded by the server for these rule checks."""

    sale_id: int
    business_id: int
    branch_id: int
    status: SaleStatus
    items: list[Mapping[str, int]] | tuple[Mapping[str, int], ...]
    has_processed_returns: bool = False


@dataclass(frozen=True)
class SaleCancellationRequest:
    """In-memory cancellation request data; this class does not persist it."""

    sale_id: int
    business_id: int
    branch_id: int
    reason: str
    status: SaleCancellationStatus = SaleCancellationStatus.PENDING


def _require_positive_integer(value, field_name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _as_sale_status(status):
    try:
        return status if isinstance(status, SaleStatus) else SaleStatus(status)
    except (TypeError, ValueError):
        raise ValueError("Unknown sale status.") from None


def _as_request_status(status):
    try:
        return (
            status
            if isinstance(status, SaleCancellationStatus)
            else SaleCancellationStatus(status)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown sale cancellation request status.") from None


def _validated_sale_items(items):
    if not isinstance(items, (list, tuple)):
        raise ValueError("Sale items must be a list or tuple.")
    if not items:
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
        raise ValueError("A trusted sale is required.")
    _require_positive_integer(sale.sale_id, "sale_id")
    _require_positive_integer(sale.business_id, "business_id")
    _require_positive_integer(sale.branch_id, "branch_id")
    _as_sale_status(sale.status)
    _validated_sale_items(sale.items)
    if type(sale.has_processed_returns) is not bool:
        raise ValueError("has_processed_returns must be a boolean.")


def _validate_request(request):
    if not isinstance(request, SaleCancellationRequest):
        raise ValueError("A sale cancellation request is required.")
    _require_positive_integer(request.sale_id, "sale_id")
    _require_positive_integer(request.business_id, "business_id")
    _require_positive_integer(request.branch_id, "branch_id")
    if not isinstance(request.reason, str) or not request.reason.strip():
        raise ValueError("A cancellation reason is required.")
    _as_request_status(request.status)


def _sale_scope(sale):
    _validate_sale(sale)
    return ResourceScope(business_id=sale.business_id, branch_id=sale.branch_id)


def _require_permission(principal, permission, resource, action):
    if not isinstance(principal, AuthenticatedPrincipal) or not has_permission(
        principal, permission, resource
    ):
        raise PermissionError(f"You are not allowed to {action} this sale.")


def _validate_request_sale_match(request, sale):
    _validate_request(request)
    _validate_sale(sale)
    if (
        request.sale_id != sale.sale_id
        or request.business_id != sale.business_id
        or request.branch_id != sale.branch_id
    ):
        raise ValueError("The cancellation request does not match this sale.")


def validate_status_transition(current_status, new_status):
    """Check that a cancellation request can move between these statuses."""
    current = _as_request_status(current_status)
    new = _as_request_status(new_status)
    if new not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Cannot change request status from {current.value} to {new.value}."
        )


def validate_sale_status_transition(current_status, new_status):
    """Only allow a Completed sale to move to Cancelled."""
    current = _as_sale_status(current_status)
    new = _as_sale_status(new_status)
    if new not in ALLOWED_SALE_STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Cannot change sale status from {current.value} to {new.value}."
        )


def validate_request_submission(principal, sale, reason, existing_requests=None):
    """Validate an employee request and return an in-memory Pending request.

    ``sale`` and ``existing_requests`` must come from trusted server-side
    storage. The duplicate check is only in-memory; it does not prevent races
    between concurrent database transactions.
    """
    resource = _sale_scope(sale)
    if _as_sale_status(sale.status) is not SaleStatus.COMPLETED:
        raise ValueError("Only completed sales can be requested for cancellation.")
    if sale.has_processed_returns:
        raise ValueError("A sale with processed returns cannot be cancelled.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A cancellation reason is required.")

    _require_permission(
        principal,
        SALE_CANCELLATION_REQUEST,
        resource,
        "request cancellation for",
    )

    requests = () if existing_requests is None else existing_requests
    if not isinstance(requests, (list, tuple)):
        raise ValueError("Existing requests must be a list or tuple.")
    for request in requests:
        _validate_request(request)
        if (
            request.sale_id == sale.sale_id
            and request.business_id == sale.business_id
            and _as_request_status(request.status) is SaleCancellationStatus.PENDING
        ):
            raise ValueError("A pending cancellation request already exists for this sale.")

    return SaleCancellationRequest(
        sale_id=sale.sale_id,
        business_id=sale.business_id,
        branch_id=sale.branch_id,
        reason=reason.strip(),
    )


def _validate_pending_request(request):
    _validate_request(request)
    if _as_request_status(request.status) is not SaleCancellationStatus.PENDING:
        raise ValueError("Only pending cancellation requests can be decided.")


def validate_request_approval(principal, request, sale):
    """Authorize approval and ensure the sale is still eligible to cancel."""
    _validate_pending_request(request)
    _validate_request_sale_match(request, sale)
    if _as_sale_status(sale.status) is not SaleStatus.COMPLETED:
        raise ValueError("Only completed sales can be cancelled.")
    if sale.has_processed_returns:
        raise ValueError("A sale with processed returns cannot be cancelled.")
    _require_permission(
        principal,
        SALE_CANCELLATION_APPROVAL,
        _sale_scope(sale),
        "approve cancellation for",
    )
    validate_status_transition(request.status, SaleCancellationStatus.APPROVED)


def validate_request_rejection(principal, request, sale, reason):
    """Authorize rejection of a Pending request and require a reason."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A rejection reason is required.")
    _validate_pending_request(request)
    _validate_request_sale_match(request, sale)
    _require_permission(
        principal,
        SALE_CANCELLATION_APPROVAL,
        _sale_scope(sale),
        "reject cancellation for",
    )
    validate_status_transition(request.status, SaleCancellationStatus.REJECTED)


def calculate_restoration_quantities(sale, request):
    """Return sold quantities to restore for an approved cancellation.

    This calculation has no side effects. A persisted Cancelled sale cannot be
    planned again, and repeated decisions are rejected by the status rules.
    The future database transaction must apply the plan atomically with the
    request and sale status updates to guarantee exactly-once restoration.
    """
    _validate_request_sale_match(request, sale)
    if _as_request_status(request.status) is not SaleCancellationStatus.APPROVED:
        raise ValueError("Only approved cancellation requests can restore inventory.")
    if sale.has_processed_returns:
        raise ValueError("A sale with processed returns cannot be cancelled.")
    validate_sale_status_transition(sale.status, SaleStatus.CANCELLED)

    quantities = {}
    for item in _validated_sale_items(sale.items):
        variant_id = item["product_variant_id"]
        quantities[variant_id] = quantities.get(variant_id, 0) + item["quantity"]
    return quantities
