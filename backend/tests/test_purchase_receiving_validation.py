"""BE-30 provisional structural checks without receiving or stock operations."""

import builtins
from copy import deepcopy
from decimal import Decimal
import io
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import backend.purchase_receiving_validation as receiving_validation
from backend.purchase_receiving_validation import validate_purchase_receiving_input


class IntegerSubclass(int):
    pass


def valid_receiving():
    return {
        "purchase_order_id": 10,
        "items": [
            {"purchase_order_line_id": 25, "received_quantity": 3},
        ],
    }


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Fail on service access, including attempted access caught by the helper."""
    attempted = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)
    monkeypatch.setattr(sqlite3, "connect", attempted)

    original_import = builtins.__import__
    forbidden = {
        "app", "db", "mysql", "pymysql", "sqlalchemy", "flask", "google",
        "gemini_provider", "ai_client", "ai_service", "replenishment",
        "inventory_snapshot_report",
    }

    def guarded_import(name, *args, **kwargs):
        if forbidden.intersection(name.split(".")):
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted
    attempted.assert_not_called()


def test_valid_receiving_input():
    assert validate_purchase_receiving_input(valid_receiving()) == {}


@pytest.mark.parametrize("quantity", [0, 1, 3, 2**100])
def test_nonnegative_builtin_integer_quantities_are_structurally_valid(quantity):
    data = valid_receiving()
    data["items"][0]["received_quantity"] = quantity

    # Zero passes structure checks only; no receipt transaction is approved.
    assert validate_purchase_receiving_input(data) == {}


@pytest.mark.parametrize("data", [None, [], (), "receiving", 10, True])
def test_input_must_be_a_dictionary(data):
    assert validate_purchase_receiving_input(data) == {
        "body": "Purchase receiving input must be a dictionary.",
    }


def test_missing_purchase_order_id():
    data = valid_receiving()
    del data["purchase_order_id"]

    assert validate_purchase_receiving_input(data) == {
        "purchase_order_id": "A valid purchase order ID is required.",
    }


@pytest.mark.parametrize("value", [
    None, 0, -1, True, False, "10", 10.0, Decimal("10"),
    IntegerSubclass(10), [], {},
])
def test_purchase_order_id_requires_a_positive_builtin_integer(value):
    data = valid_receiving()
    data["purchase_order_id"] = value

    assert validate_purchase_receiving_input(data) == {
        "purchase_order_id": "A valid purchase order ID is required.",
    }


def test_missing_items():
    assert validate_purchase_receiving_input({"purchase_order_id": 10}) == {
        "items": "At least one receiving item is required.",
    }


@pytest.mark.parametrize("items", [None, [], {}, "items", 3, True, (), ({},)])
def test_items_must_be_a_nonempty_list(items):
    data = valid_receiving()
    data["items"] = items

    assert validate_purchase_receiving_input(data) == {
        "items": "At least one receiving item is required.",
    }


@pytest.mark.parametrize("item", [None, [], (), "item", 25, True])
def test_each_item_must_be_a_dictionary(item):
    data = valid_receiving()
    data["items"] = [item]

    assert validate_purchase_receiving_input(data) == {
        "items[0]": "Item must be a dictionary.",
    }


def test_missing_line_id():
    data = valid_receiving()
    del data["items"][0]["purchase_order_line_id"]

    assert validate_purchase_receiving_input(data) == {
        "items[0].purchase_order_line_id":
            "A valid purchase order line ID is required.",
    }


@pytest.mark.parametrize("value", [
    None, 0, -1, True, False, "25", 25.0, Decimal("25"),
    IntegerSubclass(25), [], {},
])
def test_line_id_requires_a_positive_builtin_integer(value):
    data = valid_receiving()
    data["items"][0]["purchase_order_line_id"] = value

    assert validate_purchase_receiving_input(data) == {
        "items[0].purchase_order_line_id":
            "A valid purchase order line ID is required.",
    }


def test_missing_received_quantity():
    data = valid_receiving()
    del data["items"][0]["received_quantity"]

    assert validate_purchase_receiving_input(data) == {
        "items[0].received_quantity":
            "Received quantity must be a non-negative integer.",
    }


@pytest.mark.parametrize("value", [
    None, -1, True, False, "3", "0", 3.0, 0.0, 1.5,
    Decimal("3"), IntegerSubclass(3), [], {},
])
def test_received_quantity_rejects_other_types_without_coercion(value):
    data = valid_receiving()
    data["items"][0]["received_quantity"] = value
    original = deepcopy(data)

    assert validate_purchase_receiving_input(data) == {
        "items[0].received_quantity":
            "Received quantity must be a non-negative integer.",
    }
    assert data == original
    assert type(data["items"][0]["received_quantity"]) is type(value)


def test_multiple_lines_with_positive_and_zero_quantities():
    data = valid_receiving()
    data["items"].append({
        "purchase_order_line_id": 26,
        "received_quantity": 0,
    })

    assert validate_purchase_receiving_input(data) == {}


def test_errors_accumulate_in_stable_field_and_line_order():
    data = {"purchase_order_id": False, "items": [{}, None, {
        "purchase_order_line_id": "27", "received_quantity": -1,
    }]}
    expected = {
        "purchase_order_id": "A valid purchase order ID is required.",
        "items[0].purchase_order_line_id":
            "A valid purchase order line ID is required.",
        "items[0].received_quantity":
            "Received quantity must be a non-negative integer.",
        "items[1]": "Item must be a dictionary.",
        "items[2].purchase_order_line_id":
            "A valid purchase order line ID is required.",
        "items[2].received_quantity":
            "Received quantity must be a non-negative integer.",
    }

    first = validate_purchase_receiving_input(data)
    second = validate_purchase_receiving_input(data)

    assert first == second == expected
    assert list(first.items()) == list(second.items()) == list(expected.items())
    assert first is not second
    first.clear()
    assert validate_purchase_receiving_input(data) == expected


def test_invalid_id_and_items_errors_are_both_returned():
    assert validate_purchase_receiving_input({}) == {
        "purchase_order_id": "A valid purchase order ID is required.",
        "items": "At least one receiving item is required.",
    }


@pytest.mark.parametrize("invalid", [False, True])
def test_valid_and_invalid_inputs_remain_unchanged(invalid):
    data = valid_receiving()
    if invalid:
        data["items"][0]["received_quantity"] = "3"
        data["items"].append(None)
    original = deepcopy(data)
    original_items = data["items"]
    original_line = data["items"][0]

    validate_purchase_receiving_input(data)

    assert data == original
    assert data["items"] is original_items
    assert data["items"][0] is original_line


def test_repeated_lines_are_not_aggregated_or_given_a_duplicate_policy():
    data = valid_receiving()
    data["items"].append({
        "purchase_order_line_id": 25,
        "received_quantity": 4,
    })
    original = deepcopy(data)

    # This asserts structure only, not permission to process either line.
    assert validate_purchase_receiving_input(data) == {}
    assert data == original
    assert [item["received_quantity"] for item in data["items"]] == [3, 4]


def test_unknown_fields_are_ignored_without_becoming_trusted_facts():
    data = valid_receiving()
    data.update({
        "actor_id": "untrusted-client-value",
        "received_at": "untrusted-client-time",
        "authorized": False,
        "status": "untrusted-client-status",
        "inventory": {"quantity": 50},
        "receipts": [],
    })
    data["items"][0]["remaining_quantity"] = -999
    original = deepcopy(data)

    assert validate_purchase_receiving_input(data) == {}
    assert data == original


def test_module_initialization_and_validation_need_no_files_or_services(
    monkeypatch, blocked_external_services,
):
    source_path = Path(receiving_validation.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted = Mock(side_effect=AssertionError("Filesystem access attempted"))
    namespace = {"__name__": "purchase_receiving_import_probe"}
    data = valid_receiving()
    original = deepcopy(data)

    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (io, "open"), (os, "open"), (os, "write"),
            (Path, "open"), (Path, "write_text"), (Path, "write_bytes"),
        ):
            guard.setattr(owner, name, attempted)
        exec(module_code, namespace)
        validator = namespace["validate_purchase_receiving_input"]
        assert validator(data) == {}
        assert validator(data) == {}
        assert "body" in validator(None)
        assert "items" in validator({})
        assert "items[0]" in validator({"purchase_order_id": 10, "items": [None]})
        assert "items[0].received_quantity" in validator({
            "purchase_order_id": 10,
            "items": [{"purchase_order_line_id": 25, "received_quantity": -1}],
        })

    assert data == original
    attempted.assert_not_called()
    blocked_external_services.assert_not_called()
