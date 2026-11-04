"""BE-44 Phase 1: prepare an explicit AI recommendation decision.

This helper has a provisional *internal* contract, not a final Flask API or
MySQL schema. It validates only a submitted decision and optional reason.
It does not authenticate users, save decision logs, create timestamps, or
perform inventory or purchase actions.
"""


def prepare_decision_input(decision, reason=None):
    """Validate a decision and return a new dictionary.

    Supported decisions are exactly 'accepted', 'rejected', and 'deferred'.
    The optional reason may be None or any string, including an empty or
    whitespace-only string. Valid text is preserved without modification.

    Raises ValueError with a fixed, safe message for invalid inputs.
    """
    if not isinstance(decision, str) or decision not in (
        "accepted",
        "rejected",
        "deferred",
    ):
        raise ValueError("Decision must be accepted, rejected, or deferred.")

    if reason is not None and not isinstance(reason, str):
        raise ValueError("Reason must be text or None.")

    return {"decision": decision, "reason": reason}
