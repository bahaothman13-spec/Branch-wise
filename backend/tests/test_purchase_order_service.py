"""Internal BE-29 preparation tests; no persistent Purchase Order API."""
import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import secrets
import socket
import sqlite3
from unittest.mock import Mock
import uuid

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import inventory_movement_rules
import repository_contracts
import branchwise.purchasing.purchase_order_service as purchase_order_service
from branchwise.purchasing.purchase_order_service import (
    PurchaseOrderPreparation,
    prepare_purchase_order_input,
)
from branchwise.purchasing.purchase_order_validation import validate_purchase_order_input
from isolation_rules import ACCESS_DENIED_MESSAGE
from permissions import (
    PURCHASES_MANAGE,
    PURCHASES_MANAGE_AUTHORIZED,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from session_auth import AuthenticatedSession, SessionManager, TrustedSessionUser
from conftest import TestSessionStore as SessionStore


@pytest.fixture
def actor():
    return AuthenticatedSession(
        23, 0,
        AuthenticatedPrincipal(Role.OWNER, 10, None, frozenset({PURCHASES_MANAGE})),
        datetime(2030, 1, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64,
    )


@pytest.fixture
def manager(actor):
    return replace(actor, principal=AuthenticatedPrincipal(
        Role.BRANCH_MANAGER, 10, 2, frozenset({PURCHASES_MANAGE_AUTHORIZED}),
    ))


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    blocked = Mock(side_effect=AssertionError("External operation attempted"))
    for target, names in (
        (socket, ("create_connection", "getaddrinfo")),
        (socket.socket, ("connect", "connect_ex", "sendto")),
        (sqlite3, ("connect",)),
        (Engine, ("connect", "raw_connection")),
        (Session, ("add", "add_all", "execute", "flush", "commit", "delete")),
        (inventory_movement_rules, (
            "build_purchase_receipt_movements", "build_manual_adjustment_movement",
            "plan_inventory_movements",
        )),
    ):
        for name in names:
            monkeypatch.setattr(target, name, blocked)

    for contract_name in (
        "BranchRepository", "UserRepository", "BusinessSettingsRepository",
        "ProductRepository", "CategoryVariantRepository", "OperationsRepository",
    ):
        contract = getattr(repository_contracts, contract_name)
        for name in vars(contract):
            if name.endswith("_atomic"):
                monkeypatch.setattr(contract, name, blocked)

    original_import = builtins.__import__
    forbidden = {
        "db", "backend.db", "repository_contracts", "inventory_movement_rules",
        "stock_adjustment_rules", "mysql", "pymysql", "sqlalchemy",
        "sqlite3", "psycopg", "psycopg2",
    }

    def guarded_import(name, *args, **kwargs):
        if any(name == module or name.startswith(module + ".") for module in forbidden):
            return blocked(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield
    blocked.assert_not_called()


def valid_order():
    return {
        "supplier_id": 1,
        "branch_id": 2,
        "items": [{"product_id": 3, "quantity": 5, "purchase_cost": "12.50"}],
    }


def prepare(actor, data, *, branch_scope=ResourceScope(10, 2), business_branch_count=1):
    return prepare_purchase_order_input(
        data, actor=actor, branch_scope=branch_scope,
        business_branch_count=business_branch_count,
    )


def assert_denied(call):
    with pytest.raises(PermissionError) as caught:
        call()
    assert str(caught.value) == ACCESS_DENIED_MESSAGE


def test_owner_preparation_has_only_preliminary_errors(actor):
    result = prepare(actor, valid_order())
    assert dict(result.errors) == {}
    assert [field.name for field in fields(result)] == ["errors"]
    assert set(vars(result)) == {"errors"}


def test_owner_can_prepare_at_another_trusted_branch_in_own_business(actor):
    data = {**valid_order(), "branch_id": 7}
    assert not prepare(actor, data, branch_scope=ResourceScope(10, 7)).errors


@pytest.mark.parametrize("grants", [frozenset(), frozenset({PURCHASES_MANAGE_AUTHORIZED})])
def test_owner_requires_own_purchasing_grant(actor, grants):
    actor = replace(actor, principal=replace(actor.principal, permissions=grants))
    assert_denied(lambda: prepare(actor, valid_order()))


def test_manager_with_trusted_grant_and_assigned_destination(manager):
    assert not prepare(manager, valid_order()).errors


@pytest.mark.parametrize("grants", [frozenset(), frozenset({PURCHASES_MANAGE})])
def test_manager_requires_authorized_purchasing_grant(manager, grants):
    manager = replace(manager, principal=replace(manager.principal, permissions=grants))
    assert_denied(lambda: prepare(manager, valid_order()))


def test_manager_cannot_prepare_at_another_branch(manager):
    data = {**valid_order(), "branch_id": 7}
    assert_denied(lambda: prepare(manager, data, branch_scope=ResourceScope(10, 7)))


def test_employee_denied_even_with_forged_purchasing_grants(actor):
    employee = replace(actor, principal=AuthenticatedPrincipal(
        Role.EMPLOYEE, 10, 2, frozenset({PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED}),
    ))
    data = {**valid_order(), "role": "Owner", "permissions": [PURCHASES_MANAGE]}
    assert_denied(lambda: prepare(employee, data))


@pytest.mark.parametrize("bad_actor", [None, {}, "Owner", object(), 23])
def test_missing_or_malformed_actor(bad_actor):
    assert_denied(lambda: prepare(bad_actor, valid_order()))


@pytest.mark.parametrize("user_id", [None, True, False, 0, -1, "23", 23.0])
def test_invalid_authenticated_user_id(actor, user_id):
    assert_denied(lambda: prepare(replace(actor, user_id=user_id), valid_order()))


@pytest.mark.parametrize("principal", [
    None, {}, "Owner",
    AuthenticatedPrincipal("Owner", 10, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, True, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 0, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 10, 2, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 10, None, [PURCHASES_MANAGE]),
    AuthenticatedPrincipal(Role.OWNER, 10, None, frozenset({42})),
    AuthenticatedPrincipal(Role.BRANCH_MANAGER, 10, None, frozenset({PURCHASES_MANAGE_AUTHORIZED})),
])
def test_malformed_principal_fails_closed(actor, principal):
    assert_denied(lambda: prepare(replace(actor, principal=principal), valid_order()))


@pytest.mark.parametrize("scope", [
    None, {}, {"business_id": 10, "branch_id": 2},
    ResourceScope(99, 2), ResourceScope(None, 2), ResourceScope(True, 2),
    ResourceScope(0, 2), ResourceScope("10", 2),
    *(ResourceScope(10, value) for value in (None, True, False, 0, -1, "2", 2.0)),
])
def test_invalid_or_foreign_scope(actor, scope):
    assert_denied(lambda: prepare(actor, valid_order(), branch_scope=scope))


@pytest.mark.parametrize("count", [None, True, False, -1, 1.5, "1", {}, Decimal("1")])
def test_invalid_trusted_branch_count_uses_onboarding_contract(actor, count):
    with pytest.raises(ValueError, match="^branch_count must be a non-negative integer.$"):
        prepare(actor, valid_order(), business_branch_count=count)


@pytest.mark.parametrize("missing", ["actor", "branch_scope", "business_branch_count"])
def test_trusted_context_arguments_are_required(actor, missing):
    context = {
        "actor": actor, "branch_scope": ResourceScope(10, 2), "business_branch_count": 1,
    }
    del context[missing]
    with pytest.raises(TypeError, match=missing):
        prepare_purchase_order_input(valid_order(), **context)


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_onboarding_blocks_purchase_preparation(actor, manager, role):
    current = actor if role is Role.OWNER else manager
    data = {**valid_order(), "business_branch_count": 1, "branch_count": 1}
    assert_denied(lambda: prepare(current, data, business_branch_count=0))


@pytest.mark.parametrize("branch_id", [1, 7, 999999])
def test_request_destination_must_match_trusted_scope(actor, branch_id):
    data = {**valid_order(), "branch_id": branch_id}
    assert_denied(lambda: prepare(actor, data))


def test_authorization_precedes_input_validation(actor, monkeypatch):
    validator = Mock(side_effect=AssertionError("Unauthorized input validated"))
    monkeypatch.setattr(purchase_order_service, "validate_purchase_order_input", validator)
    denied = replace(actor, principal=replace(actor.principal, permissions=frozenset()))
    assert_denied(lambda: prepare(denied, {"private": "untrusted-secret"}))
    assert_denied(lambda: prepare(actor, {**valid_order(), "branch_id": 999}))
    assert_denied(lambda: prepare(actor, valid_order(), business_branch_count=0))
    validator.assert_not_called()


@pytest.mark.parametrize("data", [None, [], "order", 123, True, {}])
def test_malformed_bodies_reuse_validator(actor, data):
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)


@pytest.mark.parametrize("field", ["supplier_id", "branch_id", "items"])
def test_missing_root_fields(actor, field):
    data = valid_order()
    del data[field]
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)
    assert field in prepare(actor, data).errors


@pytest.mark.parametrize("field", ["supplier_id", "branch_id"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, "2", 2.0, Decimal("2"), []])
def test_invalid_root_ids_remain_structural_errors(actor, field, value):
    data = {**valid_order(), field: value}
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)
    assert field in prepare(actor, data).errors


def test_boolean_branch_does_not_equal_integer_scope(actor):
    data = {**valid_order(), "branch_id": True}
    result = prepare(actor, data, branch_scope=ResourceScope(10, 1))
    assert "branch_id" in result.errors


@pytest.mark.parametrize("items", [None, [], {}, "items", 12, True])
def test_empty_or_malformed_items(actor, items):
    data = {**valid_order(), "items": items}
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)


@pytest.mark.parametrize("item", [None, [], "item", 1, True])
def test_malformed_item(actor, item):
    data = {**valid_order(), "items": [item]}
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)


@pytest.mark.parametrize("references", [
    {"product_id": 3}, {"variant_id": 4}, {"product_id": 3, "variant_id": 4},
    {"product_id": None, "variant_id": 4}, {"product_id": 3, "variant_id": None},
])
def test_provisional_reference_forms_preserved(actor, references):
    data = valid_order()
    data["items"][0] = {**references, "quantity": 5, "purchase_cost": "12.50"}
    assert not prepare(actor, data).errors


@pytest.mark.parametrize("references", [{}, {"product_id": None, "variant_id": None}])
def test_item_requires_product_or_variant(actor, references):
    data = valid_order()
    data["items"][0] = {**references, "quantity": 5, "purchase_cost": "12.50"}
    assert "items[0].product" in prepare(actor, data).errors


@pytest.mark.parametrize("field", ["product_id", "variant_id"])
@pytest.mark.parametrize("value", [True, False, 0, -1, "3", 3.0, [], {}])
def test_invalid_product_and_variant_ids(actor, field, value):
    data = valid_order()
    data["items"][0][field] = value
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)
    assert f"items[0].{field}" in prepare(actor, data).errors


@pytest.mark.parametrize("field", ["quantity", "purchase_cost"])
def test_missing_item_fields(actor, field):
    data = valid_order()
    del data["items"][0][field]
    assert f"items[0].{field}" in prepare(actor, data).errors


@pytest.mark.parametrize("quantity", [None, True, False, 0, -1, 2.5, "5", Decimal("5"), []])
def test_invalid_quantities(actor, quantity):
    data = valid_order()
    data["items"][0]["quantity"] = quantity
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)
    assert "items[0].quantity" in prepare(actor, data).errors


@pytest.mark.parametrize("cost", [
    0, 12, Decimal("4.25"), Decimal("-0"), "0", " 0012.5000 ",
    "0.00000000000000000001", "1e100000",
])
def test_exact_costs_preserved_without_new_limits(actor, cost):
    data = valid_order()
    data["items"][0]["purchase_cost"] = cost
    data["items"][0]["quantity"] = 10 ** 100
    original = deepcopy(data)
    assert not prepare(actor, data).errors
    assert data == original


@pytest.mark.parametrize("cost", [
    None, True, False, -1, "-0.01", "abc", "", " ", 1.5, 0.0,
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
    "NaN", "Infinity", "-Infinity", [], {},
])
def test_invalid_purchase_costs_reuse_validator(actor, cost):
    data = valid_order()
    data["items"][0]["purchase_cost"] = cost
    assert dict(prepare(actor, data).errors) == validate_purchase_order_input(data)
    assert "items[0].purchase_cost" in prepare(actor, data).errors


@pytest.mark.parametrize("cost", ["abc", "1.23456789", Decimal("sNaN")])
@pytest.mark.parametrize("traps_invalid", [True, False])
def test_decimal_context_preserved(actor, cost, traps_invalid):
    data = valid_order()
    data["items"][0]["purchase_cost"] = cost
    with localcontext() as context:
        context.prec = 3
        context.rounding = "ROUND_DOWN"
        context.traps[InvalidOperation] = traps_invalid
        context.clear_flags()
        context.flags[Inexact] = True
        before = (context.prec, context.rounding, context.Emin, context.Emax,
                  context.capitals, context.clamp, dict(context.traps), dict(context.flags))
        prepare(actor, data)
        after = (context.prec, context.rounding, context.Emin, context.Emax,
                 context.capitals, context.clamp, dict(context.traps), dict(context.flags))
        assert after == before


@pytest.mark.parametrize("field", [
    "extra", "actor", "role", "business_id", "user_id", "permissions", "grants",
    "branch_scope", "branch_ids", "business_branch_count", "branch_count",
    "total", "totals", "status", "order_id", "reference", "stock_quantity",
])
@pytest.mark.parametrize("location", ["root", "item"])
def test_unknown_fields_and_forged_metadata_are_not_reflected(actor, field, location):
    data = valid_order()
    target = data if location == "root" else data["items"][0]
    target[field] = "untrusted-secret"
    key = "fields" if location == "root" else "items[0].fields"
    result = prepare(actor, data)
    assert dict(result.errors) == {key: "Unknown fields are not allowed."}
    assert "untrusted-secret" not in repr(result)


@pytest.mark.parametrize("case", ["valid", "invalid", "unknown", "denied"])
def test_caller_input_is_unchanged(actor, case):
    data = valid_order()
    data["items"][0]["purchase_cost"] = " 0012.5000 "
    data["items"].append({"variant_id": 4, "quantity": 1, "purchase_cost": Decimal("0")})
    if case == "invalid":
        data["items"][1]["quantity"] = False
    elif case == "unknown":
        data["permissions"] = {"nested": ["untrusted-secret"]}
        data["items"][0]["status"] = "untrusted-secret"
    elif case == "denied":
        data["branch_id"] = 999
    original = deepcopy(data)
    if case == "denied":
        assert_denied(lambda: prepare(actor, data))
    else:
        prepare(actor, data)
    assert data == original


def test_structural_and_unknown_field_errors_aggregate(actor):
    data = {"supplier_id": True, "branch_id": 2, "role": "Owner", "items": [
        None, {"quantity": 0, "purchase_cost": 1.5, "business_id": 999},
    ]}
    errors = prepare(actor, data).errors
    assert set(errors) == {
        "supplier_id", "fields", "items[0]", "items[1].product",
        "items[1].quantity", "items[1].purchase_cost", "items[1].fields",
    }


@pytest.mark.parametrize("data", [None, {}, valid_order()])
def test_preparation_and_errors_are_immutable(actor, data):
    result = prepare(actor, data)
    with pytest.raises(FrozenInstanceError):
        result.errors = {}
    with pytest.raises(TypeError):
        result.errors["injected"] = "error"
    with pytest.raises(TypeError):
        del result.errors["supplier_id"]


def test_preparation_copies_error_mapping():
    source = {"supplier_id": "A valid supplier ID is required."}
    result = PurchaseOrderPreparation(source)
    source.clear()
    assert dict(result.errors) == {"supplier_id": "A valid supplier ID is required."}


def test_empty_errors_do_not_verify_references_or_prices_or_generate_identity(actor, monkeypatch):
    generated = Mock(side_effect=AssertionError("Identity generation attempted"))
    for module, names in (
        (secrets, ("token_hex", "token_urlsafe", "token_bytes")), (uuid, ("uuid4",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, generated)
    data = {
        "supplier_id": 999999, "branch_id": 2,
        "items": [{"product_id": 999998, "variant_id": 999997,
                   "quantity": 1, "purchase_cost": "0.000000000001"}],
    }
    assert vars(prepare(actor, data)) == {"errors": {}}
    generated.assert_not_called()


def test_server_resolved_session_uses_current_trusted_grants(actor):
    user = TrustedSessionUser(23, 0, True, False, actor.principal)
    current = [user]
    manager = SessionManager(
        SessionStore(), lambda user_id: current[0] if user_id == 23 else None,
        clock=lambda: datetime(2026, 11, 17, tzinfo=timezone.utc),
    )
    issued = manager.create_session(user)
    resolved = manager.resolve_session(issued.session_token)
    assert not prepare(resolved, valid_order()).errors
    current[0] = replace(user, principal=replace(user.principal, permissions=frozenset()))
    assert_denied(lambda: prepare(manager.resolve_session(issued.session_token), valid_order()))
