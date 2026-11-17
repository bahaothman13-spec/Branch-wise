"""BE-34 provisional request-envelope validation owned by Developer 2.

Uses the variant identifier spelling in Developer 1's cached transfer rules.
This is an internal structural contract, not an approved production API.
Returns field errors without changing input. No stock, persistence, identity,
authorization, reference generation, or lifecycle operations occur here.
Integration must use approved Developer 1 rules rather than duplicate them.
"""


def validate_transfer_request_input(data):
    """Return field errors; an empty result establishes structure only.

    IDs and quantities are positive built-in integers. Unknown fields are
    rejected at both levels, including all client-supplied trusted metadata.
    Duplicate lines remain structurally valid pending an approved policy.
    """
    if not isinstance(data, dict):
        return {"body": "Transfer request must be a dictionary."}
    errors = {}
    allowed = {"source_branch_id", "destination_branch_id", "items"}
    if any(key not in allowed for key in data):
        errors["body"] = "Unknown transfer request fields are not allowed."
    for field in ("source_branch_id", "destination_branch_id"):
        value = data.get(field)
        if type(value) is not int or value <= 0:
            errors[field] = "A positive integer branch ID is required."
    if not any(field in errors for field in ("source_branch_id", "destination_branch_id")):
        if data["source_branch_id"] == data["destination_branch_id"]:
            errors["destination_branch_id"] = "Source and destination branches must be different."
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors["items"] = "At least one transfer item is required in a list."
        return errors
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            errors[prefix] = "Item must be a dictionary."
            continue
        if any(key not in {"product_variant_id", "quantity"} for key in item):
            errors[prefix] = "Unknown transfer item fields are not allowed."
        for field in ("product_variant_id", "quantity"):
            value = item.get(field)
            if type(value) is not int or value <= 0:
                errors[f"{prefix}.{field}"] = "A positive integer is required."
    return errors
