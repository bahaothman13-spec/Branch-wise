"""BE-36 Developer 2 structural receiving validation.

Provisional internal envelope: transfer_id and a nonempty items list, with
product_variant_id and quantity per line, using the existing transfer spelling
and positive built-in integer convention. Not an approved HTTP/schema contract.
Zero quantities are not receipt lines under that convention; this says nothing
about how a wholly missing delivery should be reported or settled.

An empty error dictionary establishes structure only, never receipt acceptance.
Trusted lookup, authorization, inventory verification, discrepancy decisions,
lifecycle, disposition, transactions, atomic updates and durable idempotency
remain integration responsibilities. No business calculations are duplicated.
Duplicate lines are left structurally valid pending an approved policy. No
normalization, aggregation, resource lookup or input mutation occurs here.
"""


def validate_transfer_receiving_input(data):
    """Return field-specific errors for the provisional receiving envelope."""
    if not isinstance(data, dict):
        return {"body": "Transfer receiving input must be a dictionary."}
    errors = {}
    if any(key not in {"transfer_id", "items"} for key in data):
        errors["body"] = "Unknown transfer receiving fields are not allowed."
    transfer_id = data.get("transfer_id")
    if type(transfer_id) is not int or transfer_id <= 0:
        errors["transfer_id"] = "A positive integer transfer ID is required."
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors["items"] = "At least one received item is required in a list."
        return errors
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            errors[prefix] = "Item must be a dictionary."
            continue
        if any(key not in {"product_variant_id", "quantity"} for key in item):
            errors[prefix] = "Unknown received item fields are not allowed."
        for field in ("product_variant_id", "quantity"):
            value = item.get(field)
            if type(value) is not int or value <= 0:
                errors[f"{prefix}.{field}"] = "A positive integer is required."
    return errors
