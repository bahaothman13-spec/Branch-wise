
"""Provisional purchase order input validation for BE-29.

No database access, authentication, purchase creation, or stock updates.
Final API fields and database rules must be agreed with the team.
"""

from decimal import Decimal, InvalidOperation, localcontext


def valid_id(value):
    """Accept a positive integer ID, but not a boolean."""
    return type(value) is int and value > 0


def valid_cost(value):
    """Validate a non-negative cost without using floats.

    Decimal operations run inside a local context so validation
    does not change the caller's Decimal flags.
    """
    with localcontext():
        if type(value) is int:
            amount = Decimal(value)

        elif isinstance(value, Decimal):
            amount = value

        elif isinstance(value, str) and value.strip():
            try:
                amount = Decimal(value.strip())
            except InvalidOperation:
                return False

        else:
            return False

        return amount.is_finite() and amount >= 0


def validate_purchase_order_input(data):
    """Return errors for a provisional purchase order input."""

    if not isinstance(data, dict):
        return {"body": "Purchase order must be a dictionary."}

    errors = {}

    # Validate supplier
    if not valid_id(data.get("supplier_id")):
        errors["supplier_id"] = (
            "A valid supplier ID is required."
        )

    # Validate destination branch
    if not valid_id(data.get("branch_id")):
        errors["branch_id"] = (
            "A valid destination branch ID is required."
        )

    # Validate purchase items
    items = data.get("items")

    if not isinstance(items, list) or not items:
        errors["items"] = (
            "At least one purchase item is required."
        )
        return errors

    for index, item in enumerate(items):
        prefix = f"items[{index}]"

        if not isinstance(item, dict):
            errors[prefix] = "Item must be a dictionary."
            continue

        product_id = item.get("product_id")
        variant_id = item.get("variant_id")

        # A product or variant is required
        if product_id is None and variant_id is None:
            errors[f"{prefix}.product"] = (
                "A product or variant ID is required."
            )

        # Validate product ID
        if product_id is not None and not valid_id(product_id):
            errors[f"{prefix}.product_id"] = (
                "Invalid product ID."
            )

        # Validate variant ID
        if variant_id is not None and not valid_id(variant_id):
            errors[f"{prefix}.variant_id"] = (
                "Invalid variant ID."
            )

        # Validate quantity
        quantity = item.get("quantity")

        if type(quantity) is not int or quantity <= 0:
            errors[f"{prefix}.quantity"] = (
                "Quantity must be a positive integer."
            )

        # Validate purchase cost
        purchase_cost = item.get("purchase_cost")

        if not valid_cost(purchase_cost):
            errors[f"{prefix}.purchase_cost"] = (
                "Purchase cost must be a valid "
                "non-negative amount."
            )

    return errors
