"""HTTP tests for the provisional Purchase Receiving API (BE-30 Phase 2)."""

from copy import deepcopy

import pytest
from flask import Flask

import purchase_receiving_routes
from purchase_receiving_routes import purchase_receiving_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(purchase_receiving_bp)
    return app.test_client()


def valid_receiving():
    return {
        "purchase_order_id": 10,
        "items": [{
            "purchase_order_line_id": 25,
            "received_quantity": 3,
        }],
    }


def assert_pending(response):
    assert response.status_code == 501
    assert response.get_json() == {
        "message": (
            "Purchase receiving is pending authentication, authorization, "
            "business-rule approval, and database integration."
        )
    }


@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_purchase_receiving_requires_json(client, content_type):
    response = client.post(
        "/api/purchase-receiving", data="{}", content_type=content_type
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be JSON."}


@pytest.mark.parametrize("body", [
    "", " \t\n", "{", '{"purchase_order_id": 10} trailing',
])
def test_purchase_receiving_rejects_empty_or_malformed_json(client, body):
    response = client.post(
        "/api/purchase-receiving", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Request body must be a JSON object."
    }


@pytest.mark.parametrize("body", ["null", "[]", '"receipt"', "1", "true"])
def test_purchase_receiving_rejects_non_object_json(client, body):
    response = client.post(
        "/api/purchase-receiving", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Request body must be a JSON object."
    }


@pytest.mark.parametrize("field,message", [
    ("purchase_order_id", "A valid purchase order ID is required."),
    ("items", "At least one receiving item is required."),
])
def test_purchase_receiving_requires_root_fields(client, field, message):
    receiving = valid_receiving()
    del receiving[field]

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {"errors": {field: message}}


@pytest.mark.parametrize("field,error_key,message", [
    (
        "purchase_order_id", "purchase_order_id",
        "A valid purchase order ID is required.",
    ),
    (
        "purchase_order_line_id", "items[0].purchase_order_line_id",
        "A valid purchase order line ID is required.",
    ),
])
@pytest.mark.parametrize("identifier", [None, 0, -1, True, False, "10", 10.0])
def test_purchase_receiving_rejects_invalid_identifiers(
    client, field, error_key, message, identifier,
):
    receiving = valid_receiving()
    target = receiving if field == "purchase_order_id" else receiving["items"][0]
    target[field] = identifier

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {"errors": {error_key: message}}


@pytest.mark.parametrize("items", [None, [], {}, "items", True])
def test_purchase_receiving_rejects_invalid_items_collection(client, items):
    receiving = valid_receiving()
    receiving["items"] = items

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"items": "At least one receiving item is required."}
    }


@pytest.mark.parametrize("item", [None, [], "line", 1, True])
def test_purchase_receiving_rejects_non_object_items(client, item):
    receiving = valid_receiving()
    receiving["items"] = [item]

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {"items[0]": "Item must be a dictionary."}
    }


@pytest.mark.parametrize("field,message", [
    ("purchase_order_line_id", "A valid purchase order line ID is required."),
    ("received_quantity", "Received quantity must be a non-negative integer."),
])
def test_purchase_receiving_requires_item_fields(client, field, message):
    receiving = valid_receiving()
    del receiving["items"][0][field]

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {"errors": {f"items[0].{field}": message}}


@pytest.mark.parametrize("quantity", [
    None, -1, True, False, "3", "0", 3.0, 0.0, 1.5, [], {},
])
def test_purchase_receiving_rejects_invalid_quantities_without_conversion(
    client, quantity,
):
    receiving = valid_receiving()
    receiving["items"][0]["received_quantity"] = quantity

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {
            "items[0].received_quantity": (
                "Received quantity must be a non-negative integer."
            )
        }
    }


@pytest.mark.parametrize("location", ["order", "item"])
@pytest.mark.parametrize("field,value", [
    ("business_id", 999),
    ("branch_id", 999),
    ("user_id", 999),
    ("actor_id", 999),
    ("permissions", ["receive"]),
    ("authorized", True),
    ("received_at", "client-controlled-time"),
    ("status", "client-controlled-status"),
    ("stock_quantity", 999),
])
def test_purchase_receiving_rejects_unknown_metadata(
    client, location, field, value,
):
    receiving = valid_receiving()
    target = receiving if location == "order" else receiving["items"][0]
    target[field] = value
    error_key = "fields" if location == "order" else "items[0].fields"

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {error_key: "Unknown fields are not allowed."}
    }


@pytest.mark.parametrize("field,error_key,message", [
    (
        "purchase_order_id", "purchase_order_id",
        "A valid purchase order ID is required.",
    ),
    (
        "purchase_order_line_id", "items[0].purchase_order_line_id",
        "A valid purchase order line ID is required.",
    ),
    (
        "received_quantity", "items[0].received_quantity",
        "Received quantity must be a non-negative integer.",
    ),
])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_purchase_receiving_rejects_raw_nonfinite_numeric_values(
    client, field, error_key, message, literal,
):
    tokens = {
        "purchase_order_id": "10",
        "purchase_order_line_id": "25",
        "received_quantity": "3",
    }
    tokens[field] = literal
    # Insert numeric tokens verbatim, without test-client JSON serialization.
    body = (
        '{"purchase_order_id": %(purchase_order_id)s, "items": [{'
        '"purchase_order_line_id": %(purchase_order_line_id)s, '
        '"received_quantity": %(received_quantity)s}]}'
    ) % tokens

    response = client.post(
        "/api/purchase-receiving", data=body, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.get_json() == {"errors": {error_key: message}}


@pytest.mark.parametrize("quantity", [0, 1, 3, 2**100])
def test_structurally_valid_quantities_return_not_implemented(client, quantity):
    receiving = valid_receiving()
    receiving["items"][0]["received_quantity"] = quantity

    response = client.post("/api/purchase-receiving", json=receiving)

    assert_pending(response)


def test_large_builtin_integer_ids_are_not_given_unapproved_limits(client):
    receiving = valid_receiving()
    receiving["purchase_order_id"] = 2**100
    receiving["items"][0]["purchase_order_line_id"] = 2**100

    response = client.post("/api/purchase-receiving", json=receiving)

    assert_pending(response)


def test_multiple_receiving_lines_return_not_implemented(client):
    receiving = valid_receiving()
    receiving["items"].append({
        "purchase_order_line_id": 26, "received_quantity": 0,
    })

    response = client.post("/api/purchase-receiving", json=receiving)

    assert_pending(response)


def test_duplicate_line_ids_do_not_invent_a_receiving_policy(client):
    receiving = valid_receiving()
    receiving["items"].append({
        "purchase_order_line_id": 25, "received_quantity": 4,
    })

    response = client.post("/api/purchase-receiving", json=receiving)

    assert_pending(response)


def test_purchase_receiving_preserves_combined_indexed_validator_errors(client):
    response = client.post("/api/purchase-receiving", json={
        "purchase_order_id": False,
        "business_id": 999,
        "items": [None, {
            "purchase_order_line_id": "25",
            "received_quantity": -1,
            "user_id": 999,
        }],
    })

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {
            "purchase_order_id": "A valid purchase order ID is required.",
            "items[0]": "Item must be a dictionary.",
            "items[1].purchase_order_line_id": (
                "A valid purchase order line ID is required."
            ),
            "items[1].received_quantity": (
                "Received quantity must be a non-negative integer."
            ),
            "fields": "Unknown fields are not allowed.",
            "items[1].fields": "Unknown fields are not allowed.",
        }
    }


def test_invalid_items_collection_still_rejects_unknown_root_metadata(client):
    receiving = valid_receiving()
    receiving["items"] = None
    receiving["business_id"] = 999

    response = client.post("/api/purchase-receiving", json=receiving)

    assert response.status_code == 400
    assert response.get_json() == {
        "errors": {
            "items": "At least one receiving item is required.",
            "fields": "Unknown fields are not allowed.",
        }
    }


def test_purchase_receiving_passes_unchanged_payload_to_real_validator(
    client, monkeypatch,
):
    receiving = valid_receiving()
    receiving["purchase_order_id"] = 2**100
    receiving["items"][0]["received_quantity"] = 2**100
    receiving["items"].append({
        "purchase_order_line_id": 25, "received_quantity": 0,
    })
    seen = []
    real_validator = purchase_receiving_routes.validate_purchase_receiving_input

    def record_input(data):
        seen.append((deepcopy(data), data))
        return real_validator(data)

    monkeypatch.setattr(
        purchase_receiving_routes, "validate_purchase_receiving_input", record_input
    )

    response = client.post("/api/purchase-receiving", json=receiving)

    assert_pending(response)
    assert len(seen) == 1
    before, after = seen[0]
    assert before == after == receiving
    assert type(after["purchase_order_id"]) is int
    assert all(type(item["purchase_order_line_id"]) is int for item in after["items"])
    assert all(type(item["received_quantity"]) is int for item in after["items"])
