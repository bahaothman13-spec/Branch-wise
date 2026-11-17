"""Cross-route regression checks for the isolated provisional APIs."""

import builtins
import socket
import sqlite3
from unittest.mock import Mock

import pytest
from flask import Flask

import app as main_app
from branchwise.purchasing.purchase_order_routes import purchase_order_bp
from branchwise.purchasing.purchase_receiving_routes import purchase_receiving_bp
from branchwise.suppliers.supplier_routes import supplier_bp


@pytest.fixture
def integrated_client():
    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    test_app.register_blueprint(supplier_bp)
    test_app.register_blueprint(purchase_order_bp)
    test_app.register_blueprint(purchase_receiving_bp)
    return test_app.test_client()


ROUTE_CASES = (
    ('/api/suppliers', 'suppliers.create_supplier', {'name': 'Synthetic Supplier'}),
    ('/api/purchase-orders', 'purchase_orders.create_purchase_order', {
        'supplier_id': 1, 'branch_id': 2,
        'items': [{'product_id': 3, 'quantity': 2, 'purchase_cost': '1.25'}],
    }),
    ('/api/purchase-receiving', 'purchase_receiving.receive_purchase', {
        'purchase_order_id': 4,
        'items': [{'purchase_order_line_id': 5, 'received_quantity': 1}],
    }),
)


@pytest.mark.parametrize('path,endpoint,payload', ROUTE_CASES)
def test_combined_app_has_one_post_rule_and_rejects_other_methods(
    integrated_client, path, endpoint, payload,
):
    rules = [r for r in integrated_client.application.url_map.iter_rules()
             if r.rule == path]
    assert len(rules) == 1
    assert rules[0].endpoint == endpoint
    assert rules[0].methods == {'POST', 'OPTIONS'}
    for method in ('GET', 'HEAD', 'PUT', 'PATCH', 'DELETE'):
        response = integrated_client.open(path, method=method, json=payload)
        assert response.status_code == 405
        assert set(response.headers['Allow'].replace(' ', '').split(',')) == {
            'POST', 'OPTIONS',
        }


@pytest.mark.parametrize('path,endpoint,payload', ROUTE_CASES)
def test_malformed_request_does_not_contaminate_other_blueprints(
    integrated_client, path, endpoint, payload,
):
    # Reuse one client: parsing failure must not leak into another handler.
    response = integrated_client.post(
        path, data='{', content_type='application/json',
    )
    assert response.status_code == 400
    assert response.get_json() == {'error': 'Request body must be a JSON object.'}
    for other_path, _, other_payload in ROUTE_CASES:
        response = integrated_client.post(other_path, json=other_payload)
        assert response.status_code == 501
        assert set(response.get_json()) == {'message'}


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
@pytest.mark.parametrize('body_kind', ['valid', 'malformed', 'invalid_fields'])
def test_requests_do_not_attempt_database_or_network_access(
    integrated_client, monkeypatch, path, payload, body_kind
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

    if body_kind == 'malformed':
        response = integrated_client.post(
            path, data='{', content_type='application/json',
        )
    else:
        response = integrated_client.post(
            path, json=payload if body_kind == 'valid' else {},
        )

    assert response.status_code == (501 if body_kind == 'valid' else 400)
    attempted.assert_not_called()
