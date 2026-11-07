"""HTTP tests for the provisional Purchase Order API (BE-29 Phase 2)."""

from copy import deepcopy

import pytest
from flask import Flask

import purchase_order_routes
from purchase_order_routes import purchase_order_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(purchase_order_bp)
    return app.test_client()


def valid_order():
    return {
        "supplier_id": 1,
        "branch_id": 2,
        "items": [{
            "product_id": 3,
            "quantity": 5,
            "purchase_cost": "12.50",
        }],
    }


def assert_pending(response):
    assert response.status_code == 501
    assert response.get_json() == {
        "message": (
            "Purchase order creation is pending authentication, "
            "authorization, and database integration."
        )
    }


@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_purchase_order_requires_json(client, content_type):
    response = client.post(
        "/api/purchase-orders",
        data="{}",
        content_type=content_type,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be JSON."}


@pytest.mark.parametrize("body", ["", "{", "not JSON"])
def test_purchase_order_rejects_empty_or_malformed_json(client, body):
    response = client.post(
        "/api/purchase-orders", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Request body must be a JSON object."
    }


@pytest.mark.parametrize("body", ["null", "[]", '"order"', "1", "true"])
def test_purchase_order_rejects_non_object_json(client, body):
    response = client.post(
        "/api/purchase-orders", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Request body must be a JSON object."
    }


@pytest.mark.parametrize("field", ["supplier_id", "branch_id", "items"])
def test_purchase_order_requires_root_fields(client, field):
    order = valid_order()
    del order[field]

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert field in response.get_json()["errors"]


@pytest.mark.parametrize("field,value", [
    ("supplier_id", True),
    ("supplier_id", 0),
    ("supplier_id", "1"),
    ("branch_id", False),
    ("branch_id", -1),
    ("branch_id", 2.0),
])
def test_purchase_order_rejects_invalid_root_ids(client, field, value):
    order = valid_order()
    order[field] = value

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert field in response.get_json()["errors"]


@pytest.mark.parametrize("items", [None, [], {}, "items", True])
def test_purchase_order_rejects_invalid_items_collection(client, items):
    order = valid_order()
    order["items"] = items

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert "items" in response.get_json()["errors"]


@pytest.mark.parametrize("item", [None, [], "item", 1])
def test_purchase_order_rejects_non_object_items(client, item):
    order = valid_order()
    order["items"] = [item]

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"items[0]": "Item must be a dictionary."}
    }


@pytest.mark.parametrize("field", ["quantity", "purchase_cost"])
def test_purchase_order_requires_item_fields(client, field):
    order = valid_order()
    del order["items"][0][field]

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert f"items[0].{field}" in response.get_json()["errors"]


@pytest.mark.parametrize("references", [{}, {"product_id": None, "variant_id": None}])
def test_purchase_order_requires_a_product_or_variant(client, references):
    order = valid_order()
    order["items"][0] = {
        **references, "quantity": 5, "purchase_cost": "12.50"
    }

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert "items[0].product" in response.get_json()["errors"]


@pytest.mark.parametrize("field,value", [
    ("product_id", True),
    ("product_id", 0),
    ("product_id", "3"),
    ("variant_id", False),
    ("variant_id", -1),
    ("variant_id", "4"),
])
def test_purchase_order_rejects_invalid_item_references(client, field, value):
    order = valid_order()
    order["items"][0][field] = value

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert f"items[0].{field}" in response.get_json()["errors"]


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2"])
def test_purchase_order_rejects_invalid_quantities(client, quantity):
    order = valid_order()
    order["items"][0]["quantity"] = quantity

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert "items[0].quantity" in response.get_json()["errors"]


@pytest.mark.parametrize("cost", [
    None, -1, "-0.01", "abc", "", True, False, 1.5, 0.0,
    "NaN", "Infinity", "-Infinity",
])
def test_purchase_order_rejects_invalid_costs_without_conversion(client, cost):
    order = valid_order()
    order["items"][0]["purchase_cost"] = cost

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert "items[0].purchase_cost" in response.get_json()["errors"]


@pytest.mark.parametrize("cost_literal", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_purchase_order_rejects_raw_nonfinite_numeric_costs(client, cost_literal):
    body = (
        '{"supplier_id": 1, "branch_id": 2, "items": [{'
        '"product_id": 3, "quantity": 5, "purchase_cost": '
        + cost_literal
        + '}]}'
    )

    response = client.post(
        "/api/purchase-orders", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {
            "items[0].purchase_cost": (
                "Purchase cost must be a valid non-negative amount."
            )
        }
    }


@pytest.mark.parametrize("field", [
    "business_id", "user_id", "status", "stock_quantity", "permissions",
    "branch_ids",
])
def test_purchase_order_rejects_unknown_root_metadata(client, field):
    order = valid_order()
    order[field] = 999

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"fields": "Unknown fields are not allowed."}
    }


@pytest.mark.parametrize("field", [
    "business_id", "user_id", "status", "stock_quantity", "permissions",
    "branch_id",
])
def test_purchase_order_rejects_unknown_item_metadata(client, field):
    order = valid_order()
    order["items"][0][field] = 999

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"items[0].fields": "Unknown fields are not allowed."}
    }


@pytest.mark.parametrize("cost", ["12.50", 12, 0, "0"])
def test_purchase_order_accepts_exact_costs_but_returns_not_implemented(client, cost):
    order = valid_order()
    order["items"][0]["purchase_cost"] = cost

    response = client.post("/api/purchase-orders", json=order)

    assert_pending(response)


@pytest.mark.parametrize("references", [
    {"product_id": 3},
    {"variant_id": 4},
    {"product_id": 3, "variant_id": 4},
    {"product_id": 3, "variant_id": None},
    {"product_id": None, "variant_id": 4},
])
def test_purchase_order_preserves_provisional_reference_rules(client, references):
    order = valid_order()
    order["items"][0] = {
        **references, "quantity": 5, "purchase_cost": "12.50"
    }

    response = client.post("/api/purchase-orders", json=order)

    assert_pending(response)


def test_multiple_valid_purchase_items_return_not_implemented(client):
    order = valid_order()
    order["items"].append({
        "variant_id": 4, "quantity": 2, "purchase_cost": 0
    })

    response = client.post("/api/purchase-orders", json=order)

    assert_pending(response)


def test_purchase_order_aggregates_structural_and_unknown_field_errors(client):
    response = client.post("/api/purchase-orders", json={
        "supplier_id": True,
        "branch_id": 2,
        "business_id": 999,
        "items": [None, {
            "quantity": 0,
            "purchase_cost": 1.5,
            "business_id": 999,
        }],
    })

    assert response.status_code == 400
    errors = response.get_json()["errors"]
    assert set(errors) == {
        "supplier_id", "fields", "items[0]", "items[1].product",
        "items[1].quantity", "items[1].purchase_cost", "items[1].fields",
    }
    assert errors["fields"] == "Unknown fields are not allowed."
    assert errors["items[1].fields"] == "Unknown fields are not allowed."


def test_invalid_items_collection_still_rejects_unknown_root_metadata(client):
    order = valid_order()
    order["items"] = None
    order["business_id"] = 999

    response = client.post("/api/purchase-orders", json=order)

    assert response.status_code == 400
    assert set(response.get_json()["errors"]) == {"items", "fields"}


def test_purchase_order_passes_unchanged_payload_to_existing_validator(client, monkeypatch):
    order = valid_order()
    order["items"][0]["purchase_cost"] = " 0012.5000 "
    order["items"][0]["variant_id"] = None
    order["items"].append({
        "variant_id": 4, "quantity": 1, "purchase_cost": 0
    })
    seen = []
    real_validator = purchase_order_routes.validate_purchase_order_input

    def record_input(data):
        seen.append(deepcopy(data))
        return real_validator(data)

    monkeypatch.setattr(
        purchase_order_routes, "validate_purchase_order_input", record_input
    )

    response = client.post("/api/purchase-orders", json=order)

    assert_pending(response)
    assert seen == [order]
    assert type(seen[0]["items"][1]["purchase_cost"]) is int
