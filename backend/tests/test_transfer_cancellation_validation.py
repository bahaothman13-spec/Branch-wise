from collections import UserDict
from copy import deepcopy
from decimal import Decimal

import pytest

from backend.transfer_cancellation_validation import validate_transfer_cancellation_input as validate


@pytest.mark.parametrize("value", [1, 2, 2**63, 10**100])
def test_valid_structure(value):
    assert validate({"transfer_id": value}) == {}


@pytest.mark.parametrize("data", [None, [], (), "", "{}", 1, 1.0, True, set(),
                                  UserDict({"transfer_id": 1})])
def test_malformed_body(data):
    assert validate(data) == {"body": "Transfer cancellation input must be a dictionary."}


@pytest.mark.parametrize("value", [None, True, False, 0, -1, -10**100, 1.0,
                                   "1", " 1 ", [], {}, (), Decimal("1"),
                                   float("inf"), float("nan"), b"1"])
def test_invalid_id(value):
    assert validate({"transfer_id": value}) == {
        "transfer_id": "A positive integer transfer ID is required."}


def test_missing_id():
    assert validate({}) == {"transfer_id": "A positive integer transfer ID is required."}


def test_integer_subclass():
    class Identifier(int):
        pass

    assert "transfer_id" in validate({"transfer_id": Identifier(1)})


@pytest.mark.parametrize("field", ["user_id", "actor_id", "cancelled_by", "role",
    "permissions", "authorized", "business_id", "owner_id", "source_branch_id",
    "destination_branch_id", "status", "state", "current_status", "new_status",
    "created_at", "cancelled_at", "timestamp", "inventory_movements", "stock",
    "items", "quantity", "reason", "cancellation_reason", "notes", "action",
    "idempotency_key", "unknown", "Transfer_id", 7, None, ("transfer_id",)])
@pytest.mark.parametrize("transfer_id", [1, None])
def test_unexpected_fields_and_combined_errors(field, transfer_id):
    data = {"transfer_id": transfer_id, field: {"nested": [1, 2]}}
    before = deepcopy(data)
    expected = {"body": "Unknown transfer cancellation fields are not allowed."}
    if transfer_id is None:
        expected["transfer_id"] = "A positive integer transfer ID is required."
    assert validate(data) == expected
    assert data == before


@pytest.mark.parametrize("data", [{"transfer_id": 1}, {},
    {"transfer_id": [1]}, {"transfer_id": {"nested": [1]}},
    {"transfer_id": 1, "permissions": {"nested": [True]}}])
def test_immutability_and_independent_results(data):
    before = deepcopy(data)
    result = validate(data)
    expected = result.copy()
    result["injected"] = "changed"
    assert validate(data) == expected
    assert data == before
