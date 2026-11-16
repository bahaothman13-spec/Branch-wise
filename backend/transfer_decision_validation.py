"""BE-35 pure approval/rejection envelope checks owned by Developer 2.

The internal labels transfer_id and action (approve/reject) are provisional,
not an approved HTTP or persistence contract. No optional decision fields are
specified in the available transfer documentation, so none are accepted here.
In particular, this does not impose a rejection-reason requirement.

An empty error dictionary establishes structure only. Trusted transfer lookup,
ownership, permissions, lifecycle decisions, dispatch/stock behavior, metadata,
transactions and persistence belong to later approved integration. This module
does not duplicate Developer 1's transfer lifecycle or quantity rules.
"""


def validate_transfer_decision_input(data):
    """Return field errors without mutating data or performing a decision.

    Require a positive built-in integer transfer_id and an exact action string
    of approve or reject. Reject every other key, including trusted metadata.
    Do not normalize values or infer whether the requested action is allowed.
    """
    if not isinstance(data, dict):
        return {"body": "Transfer decision must be a dictionary."}

    errors = {}
    if any(key not in {"transfer_id", "action"} for key in data):
        errors["body"] = "Unknown transfer decision fields are not allowed."

    transfer_id = data.get("transfer_id")
    if type(transfer_id) is not int or transfer_id <= 0:
        errors["transfer_id"] = "A positive integer transfer ID is required."

    action = data.get("action")
    if not isinstance(action, str) or action not in ("approve", "reject"):
        errors["action"] = "Action must be approve or reject."

    return errors
