"""Cross-route regression checks for the isolated provisional APIs."""

import builtins
import socket
import sqlite3
from unittest.mock import Mock

import pytest
from flask import Flask

import app as main_app
from purchase_order_routes import purchase_order_bp
from purchase_receiving_routes import purchase_receiving_bp
from supplier_routes import supplier_bp


@pytest.fixture
def integrated_client():
    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.register_blueprint(supplier_bp)
    test_app.register_blueprint(purchase_order_bp)
    test_app.register_blueprint(purchase_receiving_bp)
    return test_app.test_client()


@pytest.mark.parametrize(
    "path,payload,message",
    [
        (
            "/api/suppliers",
            {"name": "Synthetic Supplier"},
            "Supplier creation is pending authentication and database integration.",
        ),
        (
            "/api/purchase-orders",
            {
                "supplier_id": 1,
                "branch_id": 2,
                "items": [{"product_id": 3, "quantity": 2, "purchase_cost": "1.25"}],
            },
            (
                "Purchase order creation is pending authentication, "
                "authorization, and database integration."
            ),
        ),
        (
            "/api/purchase-receiving",
            {
                "purchase_order_id": 4,
                "items": [{"purchase_order_line_id": 5, "received_quantity": 1}],
            },
            (
                "Purchase receiving is pending authentication, authorization, "
                "business-rule approval, and database integration."
            ),
        ),
    ],
)
def test_blueprints_coexist_and_each_request_reaches_its_own_route(
    integrated_client, path, payload, message
):
    response = integrated_client.post(path, json=payload)

    assert response.status_code == 501
    assert response.get_json() == {"message": message}


def test_main_application_keeps_provisional_routes_unregistered():
    rules = {rule.rule for rule in main_app.app.url_map.iter_rules()}

    assert {
        "/api/suppliers",
        "/api/purchase-orders",
        "/api/purchase-receiving",
    }.isdisjoint(rules)


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/suppliers", {"name": "Synthetic Supplier"}),
        (
            "/api/purchase-orders",
            {
                "supplier_id": 1,
                "branch_id": 2,
                "items": [{"product_id": 3, "quantity": 2, "purchase_cost": "1.25"}],
            },
        ),
        (
            "/api/purchase-receiving",
            {
                "purchase_order_id": 4,
                "items": [{"purchase_order_line_id": 5, "received_quantity": 1}],
            },
        ),
    ],
)
def test_valid_requests_do_not_attempt_database_or_network_access(
    integrated_client, monkeypatch, path, payload
):
    attempted = Mock(side_effect=AssertionError("External operation attempted"))
    monkeypatch.setattr(sqlite3, "connect", attempted)
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)

    original_import = builtins.__import__
    forbidden_modules = {
        "db", "mysql", "pymysql", "sqlalchemy", "psycopg", "psycopg2",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in forbidden_modules:
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    response = integrated_client.post(path, json=payload)

    assert response.status_code == 501
    attempted.assert_not_called()
