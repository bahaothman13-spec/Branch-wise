"""Pure availability and minimum-stock rules for branch inventory.

The caller supplies a trusted branch-variant inventory record loaded by the
server. These helpers do not retrieve records, change stock, or create
inventory movements. Only ``available_qty`` is considered sellable stock.
"""

from collections.abc import Mapping
from enum import Enum

from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    can_access_resource,
)
from permissions import INVENTORY_VIEW, is_valid_resource_scope
from request_validation import is_positive_integer


class StockStatus(str, Enum):
    """Availability status for one branch and product variant."""

    OUT_OF_STOCK = "OUT_OF_STOCK"
    LOW_STOCK = "LOW_STOCK"
    IN_STOCK = "IN_STOCK"


_MISSING = object()


def _read_record_field(record, field_name):
    """Read a required field from a trusted mapping or record object."""
    if isinstance(record, Mapping):
        if field_name not in record:
            raise ValueError(f"Inventory record is missing {field_name}.")
        return record[field_name]

    value = getattr(record, field_name, _MISSING)
    if value is _MISSING:
        raise ValueError(f"Inventory record is missing {field_name}.")
    return value


def validate_non_negative_integer(value, field_name):
    """Validate a non-negative integer, rejecting booleans and coercions."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return value


def validate_available_quantity(available_qty):
    """Validate the trusted branch inventory ``available_qty`` value."""
    return validate_non_negative_integer(available_qty, "available_qty")


def validate_minimum_stock_level(minimum_stock_level):
    """Validate a branch-variant minimum stock level."""
    return validate_non_negative_integer(minimum_stock_level, "minimum_stock_level")


def validate_requested_quantity(quantity):
    """Validate a requested sale or stock deduction quantity."""
    if type(quantity) is not int or quantity <= 0:
        raise ValueError("quantity must be a positive integer.")
    return quantity


def get_available_quantity(inventory_record, branch_id, product_variant_id):
    """Return available stock for the requested branch-variant record.

    ``None`` represents a missing record and has zero available stock. An
    existing record must identify the requested branch and variant and must
    contain a valid ``available_qty`` field. Other quantities such as
    in-transit, under-inspection, or damaged stock are deliberately ignored.
    """
    if not is_positive_integer(branch_id):
        raise ValueError("branch_id must be a positive integer.")
    if not is_positive_integer(product_variant_id):
        raise ValueError("product_variant_id must be a positive integer.")
    if inventory_record is None:
        return 0

    record_branch_id = _read_record_field(inventory_record, "branch_id")
    record_variant_id = _read_record_field(inventory_record, "product_variant_id")
    if not is_positive_integer(record_branch_id):
        raise ValueError("Inventory record branch_id must be a positive integer.")
    if not is_positive_integer(record_variant_id):
        raise ValueError(
            "Inventory record product_variant_id must be a positive integer."
        )
    if (record_branch_id, record_variant_id) != (branch_id, product_variant_id):
        raise ValueError(
            "Inventory record does not match the requested branch and product variant."
        )

    available_qty = _read_record_field(inventory_record, "available_qty")
    return validate_available_quantity(available_qty)


def is_stock_available(available_qty, requested_quantity):
    """Return whether a positive requested quantity fits available stock."""
    available_qty = validate_available_quantity(available_qty)
    requested_quantity = validate_requested_quantity(requested_quantity)
    return requested_quantity <= available_qty


def validate_stock_availability(available_qty, requested_quantity):
    """Raise when a proposed sale or deduction exceeds available stock."""
    if not is_stock_available(available_qty, requested_quantity):
        raise ValueError("Requested quantity exceeds available stock.")
    return True


def calculate_remaining_quantity(available_qty, deduction_quantity):
    """Return remaining available stock after a valid proposed deduction."""
    available_qty = validate_available_quantity(available_qty)
    deduction_quantity = validate_requested_quantity(deduction_quantity)
    validate_stock_availability(available_qty, deduction_quantity)
    return available_qty - deduction_quantity


def classify_stock_status(available_qty, minimum_stock_level):
    """Classify stock using the configured minimum for this branch-variant."""
    available_qty = validate_available_quantity(available_qty)
    minimum_stock_level = validate_minimum_stock_level(minimum_stock_level)

    if available_qty == 0:
        return StockStatus.OUT_OF_STOCK
    if available_qty <= minimum_stock_level:
        return StockStatus.LOW_STOCK
    return StockStatus.IN_STOCK


def can_access_branch_variant_stock(
    principal,
    branch_scope,
    variant_scope,
    *,
    required_permission=INVENTORY_VIEW,
):
    """Check access using trusted principal and server-loaded resource scopes.

    The branch scope identifies the branch-variant inventory operation. The
    variant scope is checked as a related business-owned resource. Authorization
    is delegated to the existing isolation rules; request-provided identity or
    scope data must not be passed here as trusted context.
    """
    if (
        not is_valid_resource_scope(branch_scope)
        or branch_scope.branch_id is None
        or not is_valid_resource_scope(variant_scope)
        or variant_scope.branch_id is not None
    ):
        return False
    return can_access_resource(
        principal,
        required_permission,
        branch_scope,
        related_scopes=(variant_scope,),
    )


def require_branch_variant_stock_access(
    principal,
    branch_scope,
    variant_scope,
    *,
    required_permission=INVENTORY_VIEW,
):
    """Raise the existing non-disclosing access error when stock is out of scope."""
    if not can_access_branch_variant_stock(
        principal,
        branch_scope,
        variant_scope,
        required_permission=required_permission,
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)


__all__ = [
    "StockStatus",
    "calculate_remaining_quantity",
    "can_access_branch_variant_stock",
    "classify_stock_status",
    "get_available_quantity",
    "is_stock_available",
    "require_branch_variant_stock_access",
    "validate_available_quantity",
    "validate_minimum_stock_level",
    "validate_non_negative_integer",
    "validate_requested_quantity",
    "validate_stock_availability",
]
