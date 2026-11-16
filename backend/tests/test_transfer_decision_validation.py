from copy import deepcopy
from decimal import Decimal

import pytest

from backend.transfer_decision_validation import validate_transfer_decision_input as validate


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("transfer_id", [1, 2, 10**100])
def test_valid_structure_only(action, transfer_id):
    # No resource lookup, current state, permission or rejection reason needed.
    assert validate({"transfer_id": transfer_id, "action": action}) == {}


@pytest.mark.parametrize("data", [None, [], (), "", "{}", 1, 1.0, True, set()])
def test_malformed_body(data):
    assert validate(data) == {"body": "Transfer decision must be a dictionary."}


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", [], {},
                                  Decimal("1"), float("inf"), float("nan")])
def test_invalid_transfer_id(value):
    assert validate({"transfer_id": value, "action": "approve"}) == {
        "transfer_id": "A positive integer transfer ID is required."}


@pytest.mark.parametrize("value", [None, True, False, 0, 1, [], {}, (),
                                  "", "Approve", "REJECT", " approve", "reject ",
                                  "approved", "rejected", "dispatch", "cancel",
                                  "Pending", "In Transit", "approve\x00"])
def test_invalid_action(value):
    assert validate({"transfer_id": 1, "action": value}) == {
        "action": "Action must be approve or reject."}


@pytest.mark.parametrize("field", ["transfer_id", "action"])
def test_missing_field(field):
    data = {"transfer_id": 1, "action": "reject"}
    del data[field]
    assert set(validate(data)) == {field}


@pytest.mark.parametrize("field", ["actor_id", "user_id", "approved_by", "rejected_by",
                                  "role", "permissions", "authorized", "business_id",
                                  "source_branch_id", "destination_branch_id",
                                  "owner_id", "created_at", "timestamp", "decided_at",
                                  "status", "state", "current_status", "new_status",
                                  "items", "stock", "reason", "notes", "unknown",
                                  7, None, ("action",)])
@pytest.mark.parametrize("action", ["approve", "reject"])
def test_unknown_and_trusted_fields_rejected(field, action):
    data = {"transfer_id": 1, "action": action, field: {"nested": [1, 2]}}
    assert validate(data) == {
        "body": "Unknown transfer decision fields are not allowed."}


def test_all_errors_collected():
    assert set(validate({"status": "Rejected"})) == {"body", "transfer_id", "action"}


@pytest.mark.parametrize("data", [
    {"transfer_id": 1, "action": "approve"},
    {"transfer_id": 1, "action": "reject"},
    {"transfer_id": [1], "action": {"nested": ["reject"]}},
    {"transfer_id": 1, "action": "reject", "permissions": {"nested": [True]}},
    {},
])
def test_input_and_repeated_results_are_independent(data):
    before = deepcopy(data)
    first = validate(data)
    expected = deepcopy(first)
    first["injected"] = "changed"
    assert validate(data) == expected
    assert data == before


def test_integer_subclass_rejected():
    class Identifier(int):
        pass

    assert "transfer_id" in validate({"transfer_id": Identifier(1), "action": "approve"})
