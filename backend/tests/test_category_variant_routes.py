"""Focused API tests for BE-16 categories and product variants."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import create_app
from category_rules import CategoryContext, CategoryData
from permissions import ROLE_PERMISSIONS, AuthenticatedPrincipal, ResourceScope, Role
from product_rules import (
    CatalogCodeEntry,
    ProductCategoryContext,
    ProductData,
)
from repository_contracts import (
    CATEGORY_VARIANT_REPOSITORY_EXTENSION,
    CategoryListResult,
    ProductListResult,
    ProductRecord,
    ProductVariantListResult,
    ProductVariantRecord,
    PRODUCT_REPOSITORY_EXTENSION,
    RepositoryConflictError,
    RepositoryNotFoundError,
    install_category_variant_repository,
)
from session_auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    TrustedSessionUser,
    install_session_auth,
)
from variant_rules import ProductVariantContext, VariantData


BUSINESS_ID = 10
USER_ID = 23
BRANCH_ID = 22
PRODUCT_ID = 50


class MemorySessionStore:
    def __init__(self):
        self.records = {}

    def get(self, token_digest):
        return self.records.get(token_digest)

    def put(self, token_digest, record):
        self.records[token_digest] = record

    def delete(self, token_digest):
        self.records.pop(token_digest, None)


def category(category_id=7, business_id=BUSINESS_ID, name="Beverages", is_active=True):
    return CategoryContext(
        category_id=category_id,
        name=name,
        scope=ResourceScope(business_id, None),
        is_active=is_active,
    )


def product_record(product_id=PRODUCT_ID, business_id=BUSINESS_ID):
    return ProductRecord(
        product_id=product_id,
        business_id=business_id,
        data=ProductData(
            name="Green Tea",
            product_code="TEA-01",
            category_id=7,
            selling_price=Decimal("2.500"),
            cost_price=Decimal("1.000"),
        ),
        is_active=True,
    )


def variant_record(
    variant_id=901,
    *,
    product_id=PRODUCT_ID,
    business_id=BUSINESS_ID,
    code="TEA-BASE",
    size=None,
    color=None,
    selling_price=None,
    cost_price=None,
    is_default=True,
    is_active=True,
):
    return ProductVariantRecord(
        variant_id=variant_id,
        business_id=business_id,
        data=VariantData(
            product_id=product_id,
            code=code,
            size=size,
            color=color,
            selling_price=(
                None if selling_price is None else Decimal(selling_price)
            ),
            cost_price=None if cost_price is None else Decimal(cost_price),
            is_default=is_default,
        ),
        is_active=is_active,
    )


class FakeCatalogRepository:
    """Test-only scoped adapter; no persistence is installed in production."""

    def __init__(self):
        self.products = {PRODUCT_ID: product_record()}
        self.categories = {7: category(), 8: category(8, name="Cold Drinks")}
        # BE-15's atomic product creation has already made this default variant.
        self.variants = {901: variant_record()}
        self.create_category_calls = []
        self.update_category_calls = []
        self.category_status_calls = []
        self.create_variant_calls = []
        self.update_variant_calls = []
        self.variant_status_calls = []
        self.fail_create_category = None
        self.fail_create_variant = None

    def list_products(self, *, actor, search, category_id, is_active, limit, offset):
        return ProductListResult(tuple(), 0)

    def get_product(self, *, actor, product_id):
        record = self.products.get(product_id)
        if record is None or record.business_id != actor.principal.business_id:
            raise RepositoryNotFoundError()
        return record

    def get_category(self, *, actor, category_id):
        record = self.get_category_context(actor=actor, category_id=category_id)
        return ProductCategoryContext(
            category_id=record.category_id,
            scope=record.scope,
            is_active=record.is_active,
        )

    def list_catalog_codes(self, *, actor):
        business_id = actor.principal.business_id
        entries = [
            CatalogCodeEntry(
                product.business_id,
                product.data.product_code,
                "product",
                product.product_id,
            )
            for product in self.products.values()
            if product.business_id == business_id
        ]
        entries.extend(
            CatalogCodeEntry(
                record.business_id,
                record.data.code,
                "variant",
                record.variant_id,
            )
            for record in self.variants.values()
            if record.business_id == business_id
        )
        return tuple(entries)

    def create_product_atomic(self, *, actor, data):
        raise NotImplementedError

    def update_product_atomic(self, *, actor, product_id, data):
        raise NotImplementedError

    def set_product_active_atomic(self, *, actor, product_id, is_active):
        raise NotImplementedError

    def list_categories(self, *, actor, is_active, limit, offset):
        records = [
            record
            for record in self.categories.values()
            if record.scope.business_id == actor.principal.business_id
            and (is_active is None or record.is_active is is_active)
        ]
        records.sort(key=lambda record: record.category_id)
        return CategoryListResult(tuple(records[offset : offset + limit]), len(records))

    def get_category_context(self, *, actor, category_id):
        record = self.categories.get(category_id)
        if record is None or record.scope.business_id != actor.principal.business_id:
            raise RepositoryNotFoundError()
        return record

    def list_all_categories(self, *, actor):
        return tuple(
            record
            for record in self.categories.values()
            if record.scope.business_id == actor.principal.business_id
        )

    def create_category_atomic(self, *, actor, data: CategoryData):
        if self.fail_create_category is not None:
            raise self.fail_create_category
        self.create_category_calls.append(data)
        category_id = max(self.categories, default=6) + 1
        record = category(category_id, actor.principal.business_id, data.name)
        self.categories[category_id] = record
        return record

    def update_category_atomic(self, *, actor, category_id, data):
        self.update_category_calls.append((category_id, data))
        current = self.get_category_context(actor=actor, category_id=category_id)
        updated = replace(current, name=data.name)
        self.categories[category_id] = updated
        return updated

    def set_category_active_atomic(self, *, actor, category_id, is_active):
        self.category_status_calls.append((category_id, is_active))
        updated = replace(
            self.get_category_context(actor=actor, category_id=category_id),
            is_active=is_active,
        )
        self.categories[category_id] = updated
        return updated

    def list_product_variants(self, *, actor, product_id, is_active, limit, offset):
        self.get_product(actor=actor, product_id=product_id)
        records = [
            record
            for record in self.variants.values()
            if record.business_id == actor.principal.business_id
            and record.data.product_id == product_id
            and (is_active is None or record.is_active is is_active)
        ]
        records.sort(key=lambda record: record.variant_id)
        return ProductVariantListResult(tuple(records[offset : offset + limit]), len(records))

    def get_product_variant(self, *, actor, product_id, variant_id):
        self.get_product(actor=actor, product_id=product_id)
        record = self.variants.get(variant_id)
        if (
            record is None
            or record.business_id != actor.principal.business_id
            or record.data.product_id != product_id
        ):
            raise RepositoryNotFoundError()
        return record

    def list_product_variant_contexts(self, *, actor, product_id):
        self.get_product(actor=actor, product_id=product_id)
        return tuple(
            ProductVariantContext(
                variant_id=record.variant_id,
                product_id=record.data.product_id,
                scope=ResourceScope(record.business_id, None),
                is_default=record.data.is_default,
            )
            for record in self.variants.values()
            if record.business_id == actor.principal.business_id
            and record.data.product_id == product_id
        )

    def catalog_code_exists(
        self,
        *,
        actor,
        code,
        exclude_resource_type=None,
        exclude_resource_id=None,
    ):
        return any(
            entry.code.casefold() == code.strip().casefold()
            and not (
                entry.resource_type == exclude_resource_type
                and entry.resource_id == exclude_resource_id
            )
            for entry in self.list_catalog_codes(actor=actor)
        )

    def create_variant_atomic(self, *, actor, data):
        if self.fail_create_variant is not None:
            raise self.fail_create_variant
        self.create_variant_calls.append(data)
        variant_id = max(self.variants, default=900) + 1
        record = ProductVariantRecord(
            variant_id=variant_id,
            business_id=actor.principal.business_id,
            data=data,
            is_active=True,
        )
        self.variants[variant_id] = record
        return record

    def update_variant_atomic(self, *, actor, product_id, variant_id, data):
        self.update_variant_calls.append((product_id, variant_id, data))
        updated = replace(
            self.get_product_variant(
                actor=actor, product_id=product_id, variant_id=variant_id
            ),
            data=data,
        )
        self.variants[variant_id] = updated
        return updated

    def set_variant_active_atomic(self, *, actor, product_id, variant_id, is_active):
        self.variant_status_calls.append((product_id, variant_id, is_active))
        updated = replace(
            self.get_product_variant(
                actor=actor, product_id=product_id, variant_id=variant_id
            ),
            is_active=is_active,
        )
        self.variants[variant_id] = updated
        return updated


def make_client(
    *, role=Role.OWNER, business_id=BUSINESS_ID, permissions=None, with_repository=True
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
    repository = FakeCatalogRepository() if with_repository else None
    if repository is not None:
        install_category_variant_repository(app, repository)
    return app, client, repository, {CSRF_HEADER_NAME: issued.csrf_token}


def test_owner_can_create_and_list_categories_with_normalized_names():
    _, client, repository, csrf = make_client()

    created = client.post("/api/categories", json={"name": "  Snacks  "}, headers=csrf)
    listed = client.get("/api/categories?is_active=true")

    assert created.status_code == 201
    assert created.get_json()["data"] == {
        "category_id": 9,
        "name": "Snacks",
        "is_active": True,
    }
    assert listed.status_code == 200
    assert [item["name"] for item in listed.get_json()["data"]] == [
        "Beverages",
        "Cold Drinks",
        "Snacks",
    ]
    assert repository.create_category_calls == [CategoryData("Snacks")]


def test_category_name_duplicates_are_conflicts_and_do_not_write():
    _, client, repository, csrf = make_client()

    response = client.post("/api/categories", json={"name": " beverages "}, headers=csrf)

    assert response.status_code == 409
    assert "name" in response.get_json()["errors"]
    assert repository.create_category_calls == []


def test_category_update_status_and_mass_assignment_rules():
    _, client, repository, csrf = make_client()

    updated = client.patch("/api/categories/8", json={"name": "  Soft Drinks "}, headers=csrf)
    deactivated = client.patch(
        "/api/categories/8/status", json={"is_active": False}, headers=csrf
    )
    invalid = client.patch(
        "/api/categories/8", json={"name": "Bad", "business_id": 99}, headers=csrf
    )

    assert updated.status_code == 200
    assert updated.get_json()["data"]["name"] == "Soft Drinks"
    assert deactivated.status_code == 200
    assert deactivated.get_json()["data"]["is_active"] is False
    assert invalid.status_code == 400
    assert repository.update_category_calls == [(8, CategoryData("Soft Drinks"))]
    assert repository.category_status_calls == [(8, False)]


@pytest.mark.parametrize(
    ("payload", "field"),
    [({}, "name"), ({"name": "  "}, "name"), ({"name": "x" * 121}, "name")],
)
def test_category_creation_rejects_invalid_input(payload, field):
    _, client, repository, csrf = make_client()

    response = client.post("/api/categories", json=payload, headers=csrf)

    assert response.status_code == 400
    assert field in response.get_json()["errors"]
    assert repository.create_category_calls == []


def test_owner_can_create_list_update_and_status_variants():
    _, client, repository, csrf = make_client()

    created = client.post(
        f"/api/products/{PRODUCT_ID}/variants",
        json={
            "sku": " TEA-M-RED ",
            "size": " M ",
            "color": " Red ",
            "selling_price": "2.750",
            "cost_price": "1.125",
        },
        headers=csrf,
    )
    variant_id = created.get_json()["data"]["variant_id"]
    listed = client.get(f"/api/products/{PRODUCT_ID}/variants")
    updated = client.patch(
        f"/api/products/{PRODUCT_ID}/variants/{variant_id}",
        json={"sku": "TEA-M-BLUE", "color": "Blue", "selling_price": "3.000"},
        headers=csrf,
    )
    disabled = client.patch(
        f"/api/products/{PRODUCT_ID}/variants/{variant_id}/status",
        json={"is_active": False},
        headers=csrf,
    )

    assert created.status_code == 201
    assert created.get_json()["data"]["sku"] == "TEA-M-RED"
    assert created.get_json()["data"]["selling_price"] == "2.750"
    assert created.get_json()["data"]["is_default"] is False
    assert listed.status_code == 200
    assert [item["sku"] for item in listed.get_json()["data"]] == [
        "TEA-BASE",
        "TEA-M-RED",
    ]
    assert updated.status_code == 200
    assert updated.get_json()["data"]["sku"] == "TEA-M-BLUE"
    assert updated.get_json()["data"]["selling_price"] == "3.000"
    assert disabled.status_code == 200
    assert disabled.get_json()["data"]["is_active"] is False
    assert len(repository.create_variant_calls) == 1
    assert len(repository.update_variant_calls) == 1
    assert repository.variant_status_calls == [(PRODUCT_ID, variant_id, False)]


def test_variant_sku_is_unique_within_business_across_product_codes():
    _, client, repository, csrf = make_client()

    duplicate_product_code = client.post(
        f"/api/products/{PRODUCT_ID}/variants",
        json={"sku": "tea-01", "size": "M"},
        headers=csrf,
    )
    duplicate_variant_sku = client.post(
        f"/api/products/{PRODUCT_ID}/variants",
        json={"sku": "tea-base", "color": "Red"},
        headers=csrf,
    )

    assert duplicate_product_code.status_code == 409
    assert duplicate_variant_sku.status_code == 409
    assert repository.create_variant_calls == []


def test_product_can_have_only_one_default_variant():
    _, client, repository, csrf = make_client()

    response = client.post(
        f"/api/products/{PRODUCT_ID}/variants",
        json={"sku": "TEA-DEFAULT-2", "is_default": True},
        headers=csrf,
    )

    assert response.status_code == 409
    assert "is_default" in response.get_json()["errors"]
    assert repository.create_variant_calls == []


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"sku": "TEA-X"}, "size"),
        ({"sku": "TEA-X", "size": 3}, "size"),
        ({"sku": "TEA-X", "color": "Red", "selling_price": 0.5}, "selling_price"),
        ({"sku": "TEA-X", "color": "Red", "business_id": 10}, "body"),
    ],
)
def test_variant_creation_rejects_invalid_values_without_persistence(payload, field):
    _, client, repository, csrf = make_client()

    response = client.post(
        f"/api/products/{PRODUCT_ID}/variants", json=payload, headers=csrf
    )

    assert response.status_code == 400
    assert field in response.get_json()["errors"]
    assert repository.create_variant_calls == []


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_non_owners_can_view_but_cannot_directly_mutate_shared_catalog(role):
    _, client, repository, csrf = make_client(role=role)

    category_list = client.get("/api/categories")
    variant_list = client.get(f"/api/products/{PRODUCT_ID}/variants")
    category_create = client.post("/api/categories", json={"name": "New"}, headers=csrf)
    variant_create = client.post(
        f"/api/products/{PRODUCT_ID}/variants",
        json={"sku": "TEA-S", "size": "S"},
        headers=csrf,
    )
    variant_update = client.patch(
        f"/api/products/{PRODUCT_ID}/variants/901",
        json={"color": "Green"},
        headers=csrf,
    )

    assert category_list.status_code == 200
    assert variant_list.status_code == 200
    assert category_create.status_code == 403
    assert variant_create.status_code == 403
    assert variant_update.status_code == 403
    assert repository.create_category_calls == []
    assert repository.create_variant_calls == []
    assert repository.update_variant_calls == []


def test_catalog_business_isolation_hides_foreign_categories_products_and_variants():
    _, client, repository, csrf = make_client()
    _, foreign_client, _, foreign_csrf = make_client(business_id=99)

    category_update = foreign_client.patch(
        "/api/categories/7", json={"name": "Other"}, headers=foreign_csrf
    )
    variant_list = foreign_client.get(f"/api/products/{PRODUCT_ID}/variants")
    variant_update = foreign_client.patch(
        f"/api/products/{PRODUCT_ID}/variants/901",
        json={"color": "Blue"},
        headers=foreign_csrf,
    )

    assert category_update.status_code == 404
    assert variant_list.status_code == 404
    assert variant_update.status_code == 404
    assert repository.update_category_calls == []
    assert repository.update_variant_calls == []


def test_category_variant_writes_require_csrf_and_a_valid_session():
    _, client, _, _ = make_client()
    csrf_failure = client.post("/api/categories", json={"name": "New"})
    unauthenticated = create_app({"APP_ENV": "test", "TESTING": True}).test_client()

    assert csrf_failure.status_code == 403
    assert unauthenticated.get("/api/categories").status_code == 401


def test_missing_catalog_repository_returns_501_without_fake_success():
    app, client, _, csrf = make_client(with_repository=False)

    responses = [
        client.get("/api/categories"),
        client.post("/api/categories", json={"name": "New"}, headers=csrf),
        client.get(f"/api/products/{PRODUCT_ID}/variants"),
        client.post(
            f"/api/products/{PRODUCT_ID}/variants",
            json={"sku": "TEA-S", "size": "S"},
            headers=csrf,
        ),
        client.patch(
            f"/api/products/{PRODUCT_ID}/variants/901",
            json={"color": "Blue"},
            headers=csrf,
        ),
    ]

    assert CATEGORY_VARIANT_REPOSITORY_EXTENSION not in app.extensions
    assert all(response.status_code == 501 for response in responses)
    assert all(
        response.get_json()
        == {"error": "Category and variant persistence is not available."}
        for response in responses
    )


def test_invalid_pagination_and_repository_conflicts_use_standard_errors():
    _, client, repository, csrf = make_client()
    repository.fail_create_category = RepositoryConflictError()

    bad_query = client.get("/api/categories?page=0")
    conflict = client.post("/api/categories", json={"name": "New"}, headers=csrf)

    assert bad_query.status_code == 400
    assert "page" in bad_query.get_json()["errors"]
    assert conflict.status_code == 409


def test_repository_installer_requires_the_complete_category_variant_contract():
    app = create_app({"APP_ENV": "test", "TESTING": True})

    with pytest.raises(TypeError, match="required contract"):
        install_category_variant_repository(app, object())


def test_category_variant_installer_shares_the_be15_product_adapter():
    app, _, repository, _ = make_client()

    assert app.extensions[CATEGORY_VARIANT_REPOSITORY_EXTENSION] is repository
    assert app.extensions[PRODUCT_REPOSITORY_EXTENSION] is repository
