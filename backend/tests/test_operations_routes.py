"""Focused route coverage for independently wired operational APIs."""

from decimal import Decimal

from app import create_app
from permissions import ResourceScope, Role
from sale_rules import SaleProductContext, SaleVariantContext
from session_auth import CSRF_HEADER_NAME
from repository_contracts import (
    OPERATIONS_REPOSITORY_EXTENSION,
    install_operations_repository,
)

from conftest import make_role_client


def test_product_overview_requires_session_and_returns_501_without_repository():
    anonymous = create_app({"APP_ENV": "test", "TESTING": True}).test_client()
    assert anonymous.get("/api/products/8/overview").status_code == 401

    app = create_app({"APP_ENV": "test", "TESTING": True})
    client = make_role_client(app, role=Role.OWNER)
    response = client.get("/api/products/8/overview")
    invalid = client.get("/api/products/0/overview")

    assert response.status_code == 501
    assert response.get_json() == {"error": "Operational persistence is not available."}
    assert invalid.status_code == 400


def test_operations_adapter_is_explicit_and_requires_the_full_contract():
    app = create_app({"APP_ENV": "test", "TESTING": True})

    assert OPERATIONS_REPOSITORY_EXTENSION not in app.extensions
    try:
        install_operations_repository(app, object())
    except TypeError as error:
        assert "contract" in str(error)
    else:
        raise AssertionError("An incomplete adapter must not be installed.")


def test_employee_can_submit_product_snapshot_but_cannot_approve_it():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    employee = make_role_client(app, role=Role.EMPLOYEE)
    submitted = employee.post(
        "/api/product-requests",
        json={
            "change_type": "Add Product",
            "reason": "Customer demand",
            "payload": {"name": "Tea", "sku": "TEA-1"},
        },
    )
    approval = employee.post("/api/product-requests/4/approve", json={})

    assert submitted.status_code == 501
    assert submitted.get_json() == {"error": "Operational persistence is not available."}
    assert approval.status_code == 403


def test_inventory_adjustment_enforces_branch_permission_and_idempotency_key():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    employee = make_role_client(app, role=Role.EMPLOYEE)
    denied = employee.post(
        "/api/branches/30/inventory/adjustments",
        json={
            "product_variant_id": 5,
            "direction": "INCREASE",
            "quantity": 2,
            "reason": "Count correction",
        },
    )
    manager = make_role_client(app, role=Role.BRANCH_MANAGER)
    missing_key = manager.post(
        "/api/branches/30/inventory/adjustments",
        json={
            "product_variant_id": 5,
            "direction": "INCREASE",
            "quantity": 2,
            "reason": "Count correction",
        },
    )
    authorized = manager.post(
        "/api/branches/30/inventory/adjustments",
        json={
            "product_variant_id": 5,
            "direction": "INCREASE",
            "quantity": 2,
            "reason": "Count correction",
        },
        headers={"Idempotency-Key": "adjust-1"},
    )

    assert denied.status_code == 403
    assert missing_key.status_code == 400
    assert authorized.status_code == 501


def test_sales_and_return_writes_require_idempotency_and_never_fake_success():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    manager = make_role_client(app, role=Role.BRANCH_MANAGER)
    sale = {
        "branch_id": 30,
        "items": [{"product_id": 1, "product_variant_id": 2, "quantity": 1}],
    }
    no_key = manager.post("/api/sales", json=sale)
    with_key = manager.post("/api/sales", json=sale, headers={"Idempotency-Key": "sale-1"})
    return_response = manager.post(
        "/api/returns",
        json={
            "branch_id": 30,
            "reason": "Wrong item",
            "items": [{"product_variant_id": 2, "quantity": 1}],
        },
        headers={"Idempotency-Key": "return-1"},
    )

    assert no_key.status_code == 400
    assert with_key.status_code == 501
    assert return_response.status_code == 501


def test_state_changing_operations_enforce_csrf_before_repository_lookup(owner_client):
    _, client = owner_client
    response = client.post(
        "/api/product-requests",
        json={
            "change_type": "Modify Product",
            "reason": "Fix label",
            "payload": {"name": "Tea"},
        },
        headers={CSRF_HEADER_NAME: "wrong-token"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "CSRF validation failed."}


class SaleRepositoryFake:
    """Test-only adapter for route composition; it is never app persistence."""

    def __init__(self, *, catalog_business=20):
        self.catalog_business = catalog_business
        self.proposal = None

    def get_sale_creation_inputs(self, *, actor, branch_id, items):
        del actor, items
        return {
            "branch_scope": ResourceScope(20, branch_id),
            "business_branch_count": 1,
            "products": {
                40: SaleProductContext(
                    product_id=40,
                    scope=ResourceScope(self.catalog_business, None),
                    selling_price=Decimal("4.250"),
                    is_active=True,
                )
            },
            "variants": {
                90: SaleVariantContext(
                    variant_id=90,
                    product_id=40,
                    scope=ResourceScope(self.catalog_business, None),
                    selling_price=Decimal("3.750"),
                    is_active=True,
                )
            },
            "inventory_records": {
                (branch_id, 90): {
                    "branch_id": branch_id,
                    "product_variant_id": 90,
                    "available_qty": 5,
                }
            },
        }

    def create_sale_atomic(self, *, actor, proposal, idempotency_request):
        del actor, idempotency_request
        self.proposal = proposal
        return {"data": {"sale_id": 800, "total_amount": proposal.total_amount}}


def test_sale_route_uses_server_price_and_rejects_cross_business_catalog():
    app = create_app({"APP_ENV": "test", "TESTING": True})
    client = make_role_client(app, role=Role.BRANCH_MANAGER)
    repository = SaleRepositoryFake()
    app.extensions[OPERATIONS_REPOSITORY_EXTENSION] = repository
    response = client.post(
        "/api/sales",
        json={
            "branch_id": 30,
            "items": [
                {
                    "product_id": 40,
                    "product_variant_id": 90,
                    "quantity": 2,
                    "unit_price": "0.001",
                }
            ],
        },
        headers={"Idempotency-Key": "route-sale-1"},
    )

    assert response.status_code == 201
    assert response.get_json()["data"]["total_amount"] == "7.500"
    assert repository.proposal.items[0].unit_price == Decimal("3.750")

    isolated_app = create_app({"APP_ENV": "test", "TESTING": True})
    isolated_client = make_role_client(isolated_app, role=Role.BRANCH_MANAGER)
    foreign_repository = SaleRepositoryFake(catalog_business=99)
    isolated_app.extensions[OPERATIONS_REPOSITORY_EXTENSION] = foreign_repository
    denied = isolated_client.post(
        "/api/sales",
        json={
            "branch_id": 30,
            "items": [{"product_id": 40, "product_variant_id": 90, "quantity": 1}],
        },
        headers={"Idempotency-Key": "route-sale-2"},
    )
    assert denied.status_code == 403
    assert foreign_repository.proposal is None
