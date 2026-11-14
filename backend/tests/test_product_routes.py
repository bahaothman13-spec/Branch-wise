"""Focused API coverage for BE-15 product catalog routes."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import create_app
from permissions import (
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from product_rules import CatalogCodeEntry, ProductCategoryContext, ProductData
from repository_contracts import (
    PRODUCT_REPOSITORY_EXTENSION,
    ProductListResult,
    ProductRecord,
    RepositoryConflictError,
    RepositoryNotFoundError,
    install_product_repository,
)
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    TrustedSessionUser,
    install_session_auth,
)


BUSINESS_ID = 10
USER_ID = 23
BRANCH_ID = 22


class MemorySessionStore:
    def __init__(self):
        self.records = {}

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        self.records[token_digest] = record

    def delete(self, token_digest):
        self.records.pop(token_digest, None)


def product_record(
    product_id=31,
    *,
    business_id=BUSINESS_ID,
    name="Green Tea",
    product_code="TEA-01",
    category_id=7,
    selling_price="2.500",
    cost_price="0.000",
    is_active=True,
):
    return ProductRecord(
        product_id=product_id,
        business_id=business_id,
        data=ProductData(
            name=name,
            product_code=product_code,
            category_id=category_id,
            selling_price=Decimal(selling_price),
            cost_price=Decimal(cost_price),
        ),
        is_active=is_active,
    )


class FakeProductRepository:
    """Test-only adapter that keeps all product rows in local Python memory."""

    def __init__(self, products=()):
        self.products = {record.product_id: record for record in products}
        self.categories = {
            7: ProductCategoryContext(
                category_id=7,
                scope=ResourceScope(BUSINESS_ID, None),
                is_active=True,
            ),
            8: ProductCategoryContext(
                category_id=8,
                scope=ResourceScope(BUSINESS_ID, None),
                is_active=True,
            ),
        }
        self.variant_codes = []
        self.create_calls = []
        self.update_calls = []
        self.status_calls = []
        self.list_calls = []
        self.fail_create = None

    def list_products(
        self, *, actor, search, category_id, is_active, limit, offset
    ):
        self.list_calls.append(
            (actor.principal.business_id, search, category_id, is_active, limit, offset)
        )
        matches = [
            record
            for record in self.products.values()
            if record.business_id == actor.principal.business_id
            and (category_id is None or record.data.category_id == category_id)
            and (is_active is None or record.is_active is is_active)
            and (
                search is None
                or search.casefold() in record.data.name.casefold()
                or search.casefold() in record.data.product_code.casefold()
            )
        ]
        matches.sort(key=lambda record: record.product_id)
        return ProductListResult(tuple(matches[offset : offset + limit]), len(matches))

    def get_product(self, *, actor, product_id):
        record = self.products.get(product_id)
        if record is None or record.business_id != actor.principal.business_id:
            raise RepositoryNotFoundError()
        return record

    def get_category(self, *, actor, category_id):
        category = self.categories.get(category_id)
        if category is None:
            raise RepositoryNotFoundError()
        if category.scope.business_id != actor.principal.business_id:
            raise RepositoryNotFoundError()
        return category

    def list_catalog_codes(self, *, actor):
        product_codes = [
            CatalogCodeEntry(
                business_id=record.business_id,
                code=record.data.product_code,
                resource_type="product",
                resource_id=record.product_id,
            )
            for record in self.products.values()
            if record.business_id == actor.principal.business_id
        ]
        return tuple(product_codes + self.variant_codes)

    def create_product_atomic(self, *, actor, data):
        if self.fail_create is not None:
            raise self.fail_create
        self.create_calls.append(data)
        product_id = max(self.products, default=30) + 1
        record = ProductRecord(
            product_id=product_id,
            business_id=actor.principal.business_id,
            data=data,
            is_active=True,
        )
        self.products[product_id] = record
        return record

    def update_product_atomic(self, *, actor, product_id, data):
        self.update_calls.append((actor.principal.business_id, product_id, data))
        record = self.get_product(actor=actor, product_id=product_id)
        updated = replace(record, data=data)
        self.products[product_id] = updated
        return updated

    def set_product_active_atomic(self, *, actor, product_id, is_active):
        self.status_calls.append((actor.principal.business_id, product_id, is_active))
        updated = replace(
            self.get_product(actor=actor, product_id=product_id),
            is_active=is_active,
        )
        self.products[product_id] = updated
        return updated


def make_client(
    *,
    role=Role.OWNER,
    business_id=BUSINESS_ID,
    permissions=None,
    products=None,
    with_repository=True,
):
    app = create_app({"APP_ENV": "test", "TESTING": True})
    principal = AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else BRANCH_ID,
        permissions=(
            ROLE_PERMISSIONS[role]
            if permissions is None
            else frozenset(permissions)
        ),
    )
    user = TrustedSessionUser(
        user_id=USER_ID,
        access_version=3,
        is_active=True,
        is_deleted=False,
        principal=principal,
    )
    manager = install_session_auth(
        app,
        MemorySessionStore(),
        lambda user_id: user if user_id == USER_ID else None,
        clock=lambda: datetime(2026, 11, 14, tzinfo=timezone.utc),
    )
    issued = manager.create_session(user)
    client = app.test_client()
    client.set_cookie(SESSION_COOKIE_NAME, issued.session_token, path="/api")
    client.set_cookie(CSRF_COOKIE_NAME, issued.csrf_token, path="/api")
    if with_repository:
        repository = FakeProductRepository(products or ())
        install_product_repository(app, repository)
    else:
        repository = None
    return app, client, repository, {CSRF_HEADER_NAME: issued.csrf_token}


@pytest.fixture
def valid_product_payload():
    return {
        "name": "  Green Tea  ",
        "product_code": " TEA-02 ",
        "category_id": 7,
        "selling_price": "2.750",
    }


def test_owner_can_create_product_using_rules_and_atomic_repository(valid_product_payload):
    _, client, repository, csrf = make_client()

    response = client.post("/api/products", json=valid_product_payload, headers=csrf)

    assert response.status_code == 201
    assert response.get_json() == {
        "data": {
            "product_id": 31,
            "name": "Green Tea",
            "product_code": "TEA-02",
            "category_id": 7,
            "selling_price": "2.750",
            "cost_price": "0.000",
            "is_active": True,
        },
        "message": "Product created.",
    }
    assert repository.create_calls[0].selling_price == Decimal("2.750")
    assert repository.create_calls[0].cost_price == Decimal("0.000")


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"product_code": "TEA-02", "category_id": 7, "selling_price": "2"}, "name"),
        ({"name": "Tea", "product_code": "TEA-02", "category_id": True, "selling_price": "2"}, "category_id"),
        ({"name": "Tea", "product_code": "TEA-02", "category_id": 7, "selling_price": 2.5}, "selling_price"),
        ({"name": "Tea", "product_code": "TEA-02", "category_id": 7, "selling_price": "NaN"}, "selling_price"),
        ({"name": "Tea", "product_code": "TEA-02", "category_id": 7, "selling_price": "2", "business_id": 99}, "body"),
    ],
)
def test_product_create_validation_rejects_bad_fields_without_writing(payload, field):
    _, client, repository, csrf = make_client()

    response = client.post("/api/products", json=payload, headers=csrf)

    assert response.status_code == 400
    assert field in response.get_json()["errors"]
    assert repository.create_calls == []


def test_product_code_conflicts_include_products_and_variants(valid_product_payload):
    _, client, repository, csrf = make_client()
    repository.variant_codes.append(
        CatalogCodeEntry(BUSINESS_ID, "tea-02", "variant", 400)
    )

    response = client.post("/api/products", json=valid_product_payload, headers=csrf)

    assert response.status_code == 409
    assert response.get_json()["errors"]["product_code"]
    assert repository.create_calls == []


def test_product_list_supports_business_wide_search_filters_and_pagination():
    records = (
        product_record(31),
        product_record(32, name="Black Tea", product_code="TEA-02"),
        product_record(33, name="Coffee", product_code="COF-01"),
    )
    _, client, repository, _ = make_client(role=Role.BRANCH_MANAGER, products=records)

    response = client.get(
        "/api/products?q=tea&category_id=7&is_active=true&page=2&per_page=1"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert [item["product_id"] for item in body["data"]] == [32]
    assert body["pagination"] == {
        "page": 2,
        "per_page": 1,
        "total_items": 2,
        "total_pages": 2,
    }
    assert repository.list_calls == [(BUSINESS_ID, "tea", 7, True, 1, 1)]


@pytest.mark.parametrize(
    "query",
    ["?page=0", "?per_page=101", "?category_id=-1", "?is_active=yes", "?business_id=10", "?q=%20%20"],
)
def test_product_list_rejects_invalid_or_unapproved_filters(query):
    _, client, repository, _ = make_client()

    response = client.get("/api/products" + query)

    assert response.status_code == 400
    assert "errors" in response.get_json()
    assert repository.list_calls == []


def test_product_update_uses_current_product_scope_and_existing_rules():
    _, client, repository, csrf = make_client(products=(product_record(),))

    response = client.patch(
        "/api/products/31",
        json={"name": "  Updated Tea ", "selling_price": "3.125"},
        headers=csrf,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["name"] == "Updated Tea"
    assert response.get_json()["data"]["selling_price"] == "3.125"
    assert repository.update_calls[0][2].name == "Updated Tea"


def test_product_update_rejects_duplicate_code_and_unknown_category():
    _, client, repository, csrf = make_client(
        products=(product_record(), product_record(32, product_code="TEA-02"))
    )

    duplicate = client.patch(
        "/api/products/31", json={"product_code": "TEA-02"}, headers=csrf
    )
    missing_category = client.patch(
        "/api/products/31", json={"category_id": 999}, headers=csrf
    )

    assert duplicate.status_code == 409
    assert duplicate.get_json()["errors"]["product_code"]
    assert missing_category.status_code == 404
    assert repository.update_calls == []


def test_product_update_hides_cross_business_resource():
    _, client, repository, csrf = make_client(products=(product_record(31),))

    response = client.patch(
        "/api/products/31", json={"name": "Changed"}, headers=csrf
    )

    assert response.status_code == 200

    foreign_app, foreign_client, _, foreign_csrf = make_client(
        role=Role.OWNER,
        business_id=99,
        products=(product_record(31),),
    )
    del foreign_app
    foreign_response = foreign_client.patch(
        "/api/products/31", json={"name": "Changed"}, headers=foreign_csrf
    )
    assert foreign_response.status_code == 404
    assert repository.products[31].business_id == BUSINESS_ID


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_non_owners_can_view_catalog_but_cannot_write_directly(role, valid_product_payload):
    _, client, repository, csrf = make_client(role=role)

    read_response = client.get("/api/products")
    create_response = client.post(
        "/api/products", json=valid_product_payload, headers=csrf
    )
    update_response = client.patch(
        "/api/products/31", json={"name": "Changed"}, headers=csrf
    )

    assert read_response.status_code == 200
    assert create_response.status_code == 403
    assert update_response.status_code == 403
    assert repository.create_calls == []
    assert repository.update_calls == []


def test_owner_without_catalog_grant_cannot_read_or_write_products(valid_product_payload):
    _, client, repository, csrf = make_client(permissions=set())

    read_response = client.get("/api/products")
    write_response = client.post(
        "/api/products", json=valid_product_payload, headers=csrf
    )

    assert read_response.status_code == 403
    assert write_response.status_code == 403
    assert repository.list_calls == []
    assert repository.create_calls == []


def test_product_status_can_be_deactivated_and_reactivated_without_delete():
    _, client, repository, csrf = make_client(products=(product_record(),))

    deactivate = client.patch(
        "/api/products/31/status", json={"is_active": False}, headers=csrf
    )
    activate = client.patch(
        "/api/products/31/status", json={"is_active": True}, headers=csrf
    )

    assert deactivate.status_code == 200
    assert deactivate.get_json()["data"]["is_active"] is False
    assert activate.status_code == 200
    assert activate.get_json()["data"]["is_active"] is True
    assert repository.status_calls == [
        (BUSINESS_ID, 31, False),
        (BUSINESS_ID, 31, True),
    ]


def test_product_status_rejects_non_boolean_and_unapproved_fields():
    _, client, repository, csrf = make_client(products=(product_record(),))

    invalid_value = client.patch(
        "/api/products/31/status", json={"is_active": "false"}, headers=csrf
    )
    mass_assignment = client.patch(
        "/api/products/31/status",
        json={"is_active": False, "business_id": 99},
        headers=csrf,
    )

    assert invalid_value.status_code == 400
    assert invalid_value.get_json()["errors"]["is_active"] == "Must be a boolean."
    assert mass_assignment.status_code == 400
    assert "business_id" not in mass_assignment.get_data(as_text=True)
    assert repository.status_calls == []


def test_product_write_requires_csrf_and_valid_session():
    _, client, _, _ = make_client()
    csrf_failure = client.patch("/api/products/31/status", json={"is_active": False})

    unauthenticated = create_app({"APP_ENV": "test", "TESTING": True}).test_client()
    unauth_failure = unauthenticated.get("/api/products")

    assert csrf_failure.status_code == 403
    assert csrf_failure.get_json() == {"error": "CSRF validation failed."}
    assert unauth_failure.status_code == 401


def test_product_persistence_is_explicit_and_defaults_to_501(valid_product_payload):
    app, client, _, csrf = make_client(with_repository=False)

    listing = client.get("/api/products")
    creating = client.post("/api/products", json=valid_product_payload, headers=csrf)
    updating = client.patch(
        "/api/products/31", json={"name": "Changed"}, headers=csrf
    )
    status_change = client.patch(
        "/api/products/31/status", json={"is_active": False}, headers=csrf
    )

    assert PRODUCT_REPOSITORY_EXTENSION not in app.extensions
    assert all(
        response.status_code == 501
        for response in (listing, creating, updating, status_change)
    )
    assert all(
        response.get_json() == {"error": "Product persistence is not available."}
        for response in (listing, creating, updating, status_change)
    )


def test_repository_conflict_and_not_found_errors_have_standard_status_codes(
    valid_product_payload,
):
    _, client, repository, csrf = make_client()
    repository.fail_create = RepositoryConflictError()

    conflict = client.post("/api/products", json=valid_product_payload, headers=csrf)
    missing = client.patch(
        "/api/products/999", json={"name": "Missing"}, headers=csrf
    )

    assert conflict.status_code == 409
    assert conflict.get_json() == {
        "error": "The request conflicts with the current resource state."
    }
    assert missing.status_code == 404


def test_product_repository_installer_requires_complete_contract():
    app = create_app({"APP_ENV": "test", "TESTING": True})

    with pytest.raises(TypeError, match="required contract"):
        install_product_repository(app, object())
