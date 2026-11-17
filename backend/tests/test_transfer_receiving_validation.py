from copy import deepcopy
from decimal import Decimal

import pytest

from branchwise.transfers.transfer_receiving_validation import validate_transfer_receiving_input as validate


def receipt():
    return {"transfer_id": 1, "items": [{"product_variant_id": 2, "quantity": 1}]}


@pytest.mark.parametrize("value", [1, 2, 10**100])
def test_valid(value):
    assert validate({"transfer_id": value, "items": [
        {"product_variant_id": value, "quantity": value}]}) == {}


@pytest.mark.parametrize("value", [None, [], (), "{}", 1, True, 1.0, set()])
def test_body(value):
    assert validate(value) == {"body": "Transfer receiving input must be a dictionary."}


@pytest.mark.parametrize("field", ["transfer_id", "product_variant_id", "quantity"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", [], {},
                                   Decimal("1"), float("nan"), float("inf")])
def test_numbers(field, value):
    data = receipt()
    target = data if field == "transfer_id" else data["items"][0]
    target[field] = value
    path = field if field == "transfer_id" else f"items[0].{field}"
    assert set(validate(data)) == {path}


@pytest.mark.parametrize("field", ["transfer_id", "items", "product_variant_id", "quantity"])
def test_required(field):
    data = receipt()
    del (data if field in data else data["items"][0])[field]
    path = field if field in ("transfer_id", "items") else f"items[0].{field}"
    assert set(validate(data)) == {path}


@pytest.mark.parametrize("value", [None, [], (), {}, "items", True, 1])
def test_items(value):
    data = receipt()
    data["items"] = value
    assert set(validate(data)) == {"items"}


@pytest.mark.parametrize("value", [None, [], (), "item", True, 1])
def test_malformed_line(value):
    data = receipt()
    data["items"].append(value)
    assert set(validate(data)) == {"items[1]"}


@pytest.mark.parametrize("field", ["actor_id", "user_id", "received_by", "role",
    "permissions", "authorized", "business_id", "owner_id", "source_branch_id",
    "destination_branch_id", "status", "current_status", "complete_transfer",
    "created_at", "received_at", "timestamp", "reference", "receipt_id",
    "effect_reference", "source_reference", "idempotency_key", "stock",
    "dispatched_items", "previously_received_items", "discrepancy", "disposition",
    "received_quantity", "notes", "unknown", None, 7, ("field",)])
@pytest.mark.parametrize("level", ["body", "item"])
def test_unknown_fields(field, level):
    data = receipt()
    target = data if level == "body" else data["items"][0]
    target[field] = {"nested": [1]}
    assert set(validate(data)) == {"body" if level == "body" else "items[0]"}


@pytest.mark.parametrize("invalid", [False, True])
def test_immutable_and_independent_results(invalid):
    data = receipt()
    data["items"].append(deepcopy(data["items"][0]))
    if invalid:
        data["items"].append({"quantity": [], "permissions": {"nested": [True]}})
    before = deepcopy(data)
    first = validate(data)
    assert bool(first) is invalid
    expected = first.copy()
    first["injected"] = "changed"
    assert validate(data) == expected
    assert data == before


def test_collect_errors():
    assert set(validate({"status": "Completed", "items": [{}, None]})) == {
        "body", "transfer_id", "items[0].product_variant_id",
        "items[0].quantity", "items[1]"}


@pytest.mark.parametrize("field", ["transfer_id", "product_variant_id", "quantity"])
def test_integer_subclass(field):
    class Number(int):
        pass
    data = receipt()
    (data if field == "transfer_id" else data["items"][0])[field] = Number(1)
    assert validate(data)


def test_structure_does_not_require_trusted_records_or_decide_discrepancies():
    data = receipt()
    data["items"].append({"product_variant_id": 999, "quantity": 10**100})
    assert validate(data) == {}
