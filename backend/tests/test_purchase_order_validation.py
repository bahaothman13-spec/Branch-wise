
"""Unit tests for BE-29 purchase order input validation."""

from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from backend.purchase_order_validation import validate_purchase_order_input


def valid_order():
    return {
        "supplier_id": 1,
        "branch_id": 2,
        "items": [
            {
                "product_id": 3,
                "quantity": 5,
                "purchase_cost": "12.50",
            }
        ],
    }


def test_valid_purchase_order():
    assert validate_purchase_order_input(valid_order()) == {}


def test_valid_variant_order():
    order = valid_order()

    order["items"][0] = {
        "variant_id": 4,
        "quantity": 2,
        "purchase_cost": Decimal("4.25"),
    }

    assert validate_purchase_order_input(order) == {}


def test_multiple_valid_items():
    order = valid_order()

    order["items"].append({
        "product_id": 7,
        "quantity": 3,
        "purchase_cost": 10,
    })

    assert validate_purchase_order_input(order) == {}


@pytest.mark.parametrize("bad_input", [
    None,
    [],
    "purchase order",
    123,
])
def test_invalid_order_body(bad_input):
    errors = validate_purchase_order_input(bad_input)

    assert "body" in errors


@pytest.mark.parametrize("field,bad_value", [
    ("supplier_id", None),
    ("supplier_id", 0),
    ("supplier_id", -1),
    ("supplier_id", True),
    ("supplier_id", "1"),
    ("branch_id", None),
    ("branch_id", 0),
    ("branch_id", False),
    ("branch_id", "2"),
])
def test_invalid_order_ids(field, bad_value):
    order = valid_order()
    order[field] = bad_value

    errors = validate_purchase_order_input(order)

    assert field in errors


@pytest.mark.parametrize("bad_items", [
    None,
    [],
    {},
    "product",
    12,
])
def test_invalid_items_collection(bad_items):
    order = valid_order()
    order["items"] = bad_items

    errors = validate_purchase_order_input(order)

    assert "items" in errors


@pytest.mark.parametrize("field,bad_value", [
    ("product_id", 0),
    ("product_id", True),
    ("product_id", "3"),
    ("quantity", 0),
    ("quantity", -2),
    ("quantity", 2.5),
    ("quantity", True),
    ("quantity", "5"),
    ("purchase_cost", -1),
    ("purchase_cost", "-0.01"),
    ("purchase_cost", "abc"),
    ("purchase_cost", ""),
    ("purchase_cost", True),
    ("purchase_cost", 1.5),
    ("purchase_cost", Decimal("NaN")),
    ("purchase_cost", Decimal("Infinity")),
])
def test_invalid_item_fields(field, bad_value):
    order = valid_order()
    order["items"][0][field] = bad_value

    errors = validate_purchase_order_input(order)

    assert f"items[0].{field}" in errors


def test_missing_product_and_variant():
    order = valid_order()
    order["items"][0].pop("product_id")

    errors = validate_purchase_order_input(order)

    assert "items[0].product" in errors


def test_invalid_item_type():
    order = valid_order()
    order["items"] = [None]

    errors = validate_purchase_order_input(order)

    assert "items[0]" in errors


def test_purchase_order_input_is_not_modified():
    order = valid_order()
    original = deepcopy(order)

    validate_purchase_order_input(order)

    assert order == original


def test_invalid_cost_does_not_change_decimal_context():
    order = valid_order()
    order["items"][0]["purchase_cost"] = "abc"

    with localcontext() as context:
        context.clear_flags()

        errors = validate_purchase_order_input(order)

        assert "items[0].purchase_cost" in errors
        assert not any(context.flags.values())

