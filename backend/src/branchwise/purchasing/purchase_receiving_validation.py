"""BE-30 Phase 1: provisional purchase receiving input validation.

Internal input: purchase_order_id and a nonempty items list containing
purchase_order_line_id and received_quantity. Field names and built-in integer
constraints are implementation assumptions, not approved API or database rules.
Zero received quantity passes structural checks only; it does not approve a
zero-quantity receipt transaction. An empty error dictionary never means that
a receiving operation is authorized or safe to execute.

Unknown fields are ignored, not trusted or whitelisted for persistence. A future
trusted service must supply the responsible user and authoritative receiving
timestamp, load and authorize the order and lines, and enforce the approved
atomic receipt, authoritative-stock, and append-only movement-ledger contract.

No I/O, record creation, stock or status changes, remaining-quantity calculation,
partial/over-receiving policy, duplicate-line policy, or idempotency guarantees
are provided. Inputs stay unchanged; lines and quantities are never aggregated.
"""


def validate_purchase_receiving_input(data):
    """Return deterministic field errors for the provisional input structure."""
    if not isinstance(data, dict):
        return {"body": "Purchase receiving input must be a dictionary."}

    errors = {}

    purchase_order_id = data.get("purchase_order_id")
    if type(purchase_order_id) is not int or purchase_order_id <= 0:
        errors["purchase_order_id"] = "A valid purchase order ID is required."

    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors["items"] = "At least one receiving item is required."
        return errors

    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            errors[prefix] = "Item must be a dictionary."
            continue

        purchase_order_line_id = item.get("purchase_order_line_id")
        if type(purchase_order_line_id) is not int or purchase_order_line_id <= 0:
            errors[f"{prefix}.purchase_order_line_id"] = (
                "A valid purchase order line ID is required."
            )

        received_quantity = item.get("received_quantity")
        if type(received_quantity) is not int or received_quantity < 0:
            errors[f"{prefix}.received_quantity"] = (
                "Received quantity must be a non-negative integer."
            )

    return errors
