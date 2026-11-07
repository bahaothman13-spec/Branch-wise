"""Tests for the provisional Supplier API (BE-28 Phase 2)."""

import pytest
from flask import Flask

from supplier_routes import supplier_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(supplier_bp)
    return app.test_client()


def test_supplier_requires_json(client):
    response = client.post(
        "/api/suppliers",
        data="plain text",
        content_type="text/plain",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Request body must be JSON."


def test_supplier_rejects_non_object_json(client):
    response = client.post("/api/suppliers", json=["supplier"])

    assert response.status_code == 400
    assert response.get_json()["error"] == "Request body must be a JSON object."


def test_supplier_requires_name(client):
    response = client.post("/api/suppliers", json={})

    assert response.status_code == 400
    assert "name" in response.get_json()["errors"]


def test_supplier_rejects_invalid_email(client):
    response = client.post(
        "/api/suppliers",
        json={"name": "Test Supplier", "email": "invalid-email"},
    )

    assert response.status_code == 400
    assert "email" in response.get_json()["errors"]


def test_supplier_rejects_unknown_fields(client):
    response = client.post(
        "/api/suppliers",
        json={"name": "Test Supplier", "business_id": 999},
    )

    assert response.status_code == 400
    assert "fields" in response.get_json()["errors"]


def test_supplier_rejects_invalid_phone_type(client):
    response = client.post(
        "/api/suppliers",
        json={"name": "Test Supplier", "phone": 12345},
    )

    assert response.status_code == 400
    assert "phone" in response.get_json()["errors"]


def test_valid_supplier_returns_not_implemented(client):
    response = client.post(
        "/api/suppliers",
        json={
            "name": "Test Supplier",
            "contact_person": "Ahmad",
            "phone": "0791234567",
            "email": "supplier@example.com",
            "address": "Amman",
            "notes": "Test record",
        },
    )

    assert response.status_code == 501
    assert "pending authentication" in response.get_json()["message"]
