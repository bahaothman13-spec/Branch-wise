"""Pure business rules for product and variant change requests.

These helpers validate requests only. They do not save requests, change their
status, or update active products. The caller must build trusted request scope
from server-side user data and load existing requests from a trusted source.
"""

from dataclasses import dataclass
from enum import Enum

from permissions import (
    PRODUCT_CATALOG_APPROVAL,
    PRODUCT_REQUEST_COMMENT,
    PRODUCT_REQUEST_REVIEW,
    PRODUCT_REQUEST_SUBMIT,
    AuthenticatedPrincipal,
    ResourceScope,
    has_permission,
)


class ProductChangeType(str, Enum):
    """Product or variant operation requested by an employee."""

    ADD_PRODUCT = "Add Product"
    MODIFY_PRODUCT = "Modify Product"
    ADD_VARIANT = "Add Variant"
    MODIFY_VARIANT = "Modify Variant"


class ProductRequestStatus(str, Enum):
    """Statuses for a product or variant request."""

    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


@dataclass(frozen=True)
class ProductRequest:
    """Trusted scope and status needed to check a product change request."""

    business_id: int
    branch_id: int
    change_type: ProductChangeType
    is_shared_catalog: bool = False
    status: ProductRequestStatus = ProductRequestStatus.PENDING


def _as_change_type(change_type):
    try:
        return (
            change_type
            if isinstance(change_type, ProductChangeType)
            else ProductChangeType(change_type)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown product request change type.") from None


def _as_status(status):
    try:
        return (
            status
            if isinstance(status, ProductRequestStatus)
            else ProductRequestStatus(status)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown product request status.") from None


def _validate_request(request):
    if not isinstance(request, ProductRequest):
        raise ValueError("A product request is required.")
    if type(request.business_id) is not int or request.business_id <= 0:
        raise ValueError("business_id must be a positive integer.")
    if type(request.branch_id) is not int or request.branch_id <= 0:
        raise ValueError("branch_id must be a positive integer.")
    _as_change_type(request.change_type)
    _as_status(request.status)
    if type(request.is_shared_catalog) is not bool:
        raise ValueError("is_shared_catalog must be a boolean.")


def _request_scope(request):
    _validate_request(request)
    return ResourceScope(
        business_id=request.business_id,
        branch_id=request.branch_id,
    )


def _require_permission(principal, permission, resource, action):
    if not isinstance(principal, AuthenticatedPrincipal) or not has_permission(
        principal, permission, resource
    ):
        raise PermissionError(f"You are not allowed to {action} this product request.")


def validate_request_submission(
    principal, change_type, *, is_shared_catalog=False
):
    """Validate an employee submission and return an in-memory Pending request.

    Business and branch scope come from the authenticated principal. This
    function does not persist the request or change the active catalog.
    """
    _as_change_type(change_type)
    if type(is_shared_catalog) is not bool:
        raise ValueError("is_shared_catalog must be a boolean.")

    # Use the employee's trusted branch for the resource scope. Missing or
    # invalid scope is denied by the permission check and explicit validation.
    resource = ResourceScope(
        business_id=getattr(principal, "business_id", None),
        branch_id=getattr(principal, "branch_id", None),
    )
    _require_permission(
        principal,
        PRODUCT_REQUEST_SUBMIT,
        resource,
        "submit",
    )
    if type(principal.business_id) is not int or principal.business_id <= 0:
        raise ValueError("business_id must be a positive integer.")
    if type(principal.branch_id) is not int or principal.branch_id <= 0:
        raise ValueError("branch_id must be a positive integer.")

    return ProductRequest(
        business_id=principal.business_id,
        branch_id=principal.branch_id,
        change_type=_as_change_type(change_type),
        is_shared_catalog=is_shared_catalog,
    )


def validate_request_review(principal, request):
    """Check that a branch manager can review a request from their branch."""
    resource = _request_scope(request)
    _require_permission(
        principal,
        PRODUCT_REQUEST_REVIEW,
        resource,
        "review",
    )


def validate_request_comment(principal, request, comment):
    """Check comment permission and require a non-empty comment."""
    if not isinstance(comment, str) or not comment.strip():
        raise ValueError("A comment is required.")
    resource = _request_scope(request)
    _require_permission(
        principal,
        PRODUCT_REQUEST_COMMENT,
        resource,
        "comment on",
    )


def _validate_pending_request(request):
    _validate_request(request)
    if _as_status(request.status) is not ProductRequestStatus.PENDING:
        raise ValueError("Only pending product requests can be decided.")


def validate_request_approval(principal, request):
    """Check an approval; only the same-business owner can pass this rule.

    This function deliberately does not set the status or apply catalog data.
    """
    _validate_pending_request(request)
    _require_permission(
        principal,
        PRODUCT_CATALOG_APPROVAL,
        _request_scope(request),
        "approve",
    )


def validate_request_rejection(principal, request, reason):
    """Check a rejection and require its reason; no request is changed here."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A rejection reason is required.")
    _validate_pending_request(request)
    _require_permission(
        principal,
        PRODUCT_CATALOG_APPROVAL,
        _request_scope(request),
        "reject",
    )
