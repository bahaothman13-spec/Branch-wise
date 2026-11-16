"""BE-37 Developer 2 pure structural cancellation validation.

The internal transfer_id envelope follows BE-35/BE-36; it is provisional,
not an approved HTTP or database contract. No optional cancellation fields
or reason requirement are specified by the available contracts.

An empty error dictionary establishes structure only. Trusted transfer lookup,
authentication, authorization, ownership, approved cancellation eligibility,
lifecycle transitions and post-dispatch policy remain integration concerns.
This helper performs no stock restoration/reversal, inventory movements,
atomic database transactions or durable idempotency. It does not establish
that any transfer status permits cancellation or duplicate Developer 1 rules.
"""


def validate_transfer_cancellation_input(data):
    """Return field errors without mutation, coercion or cancellation."""
    if not isinstance(data, dict):
        return {"body": "Transfer cancellation input must be a dictionary."}

    errors = {}
    if any(key != "transfer_id" for key in data):
        errors["body"] = "Unknown transfer cancellation fields are not allowed."
    transfer_id = data.get("transfer_id")
    if type(transfer_id) is not int or transfer_id <= 0:
        errors["transfer_id"] = "A positive integer transfer ID is required."
    return errors
