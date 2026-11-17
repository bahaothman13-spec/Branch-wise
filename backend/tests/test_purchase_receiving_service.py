"""Internal BE-30 preparation tests; no executable receipt or persistence."""

import builtins
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
import transaction_safety_rules
import branchwise.purchasing.purchase_receiving_service as receiving_service
from branchwise.purchasing.purchase_receiving_service import (
    PurchaseReceivingPreparation,
    prepare_purchase_receiving_input,
)
from branchwise.purchasing.purchase_receiving_validation import (
    ALLOWED_FIELDS,
    ALLOWED_ITEM_FIELDS,
    validate_purchase_receiving_input,
)
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


class IntegerSubclass(int):
    pass


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
    """Guard actual effects as well as any attempted, caught operation."""
    blocked = Mock(side_effect=AssertionError("External operation attempted"))
    for target, names in (
        (socket, ("create_connection", "getaddrinfo")),
        (socket.socket, ("connect", "connect_ex", "sendto")),
        (sqlite3, ("connect",)),
        (Engine, ("connect", "raw_connection")),
        (Session, ("add", "add_all", "execute", "flush", "commit", "delete")),
        (inventory_movement_rules, tuple(
            name for name in vars(inventory_movement_rules)
            if name.startswith("build_") or name == "plan_inventory_movements"
        )),
        (transaction_safety_rules, (
            "create_idempotency_request", "decide_idempotency",
            "ordered_inventory_balance_keys",
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
            if not name.startswith("_") and callable(getattr(contract, name)):
                monkeypatch.setattr(contract, name, blocked)

    original_import = builtins.__import__
    forbidden = {
        "db", "backend.db", "repository_contracts", "inventory_movement_rules",
        "stock_rules", "stock_adjustment_rules", "transaction_safety_rules",
        "mysql", "pymysql", "sqlalchemy", "sqlite3", "psycopg", "psycopg2",
    }

    def guarded_import(name, *args, **kwargs):
        if any(name == module or name.startswith(module + ".") for module in forbidden):
            return blocked(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield
    blocked.assert_not_called()


def valid_receiving():
    return {
        "purchase_order_id": 10,
        "items": [{"purchase_order_line_id": 25, "received_quantity": 3}],
    }


def prepare(actor, data, *, branch_scope=ResourceScope(10, 2), business_branch_count=1):
    return prepare_purchase_receiving_input(
        data, actor=actor, branch_scope=branch_scope,
        business_branch_count=business_branch_count,
    )


def assert_denied(call):
    with pytest.raises(PermissionError) as caught:
        call()
    assert str(caught.value) == ACCESS_DENIED_MESSAGE


def test_authorized_owner_returns_only_preliminary_errors(actor):
    result = prepare(actor, valid_receiving())
    assert dict(result.errors) == {}
    assert [field.name for field in fields(result)] == ["errors"]
    assert set(vars(result)) == {"errors"}


def test_owner_can_prepare_at_another_trusted_branch_in_own_business(actor):
    assert not prepare(actor, valid_receiving(), branch_scope=ResourceScope(10, 7)).errors


@pytest.mark.parametrize("grants", [frozenset(), frozenset({PURCHASES_MANAGE_AUTHORIZED})])
def test_owner_requires_own_purchasing_grant(actor, grants):
    actor = replace(actor, principal=replace(actor.principal, permissions=grants))
    assert_denied(lambda: prepare(actor, valid_receiving()))


def test_manager_with_trusted_grant_and_assigned_destination(manager):
    assert not prepare(manager, valid_receiving()).errors


@pytest.mark.parametrize("grants", [frozenset(), frozenset({PURCHASES_MANAGE})])
def test_manager_requires_authorized_purchasing_grant(manager, grants):
    manager = replace(manager, principal=replace(manager.principal, permissions=grants))
    assert_denied(lambda: prepare(manager, valid_receiving()))


def test_manager_cannot_prepare_at_another_branch(manager):
    assert_denied(lambda: prepare(
        manager, valid_receiving(), branch_scope=ResourceScope(10, 7),
    ))


@pytest.mark.parametrize("grants", [
    frozenset(), frozenset({PURCHASES_MANAGE}),
    frozenset({PURCHASES_MANAGE_AUTHORIZED}),
    frozenset({PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED}),
])
def test_employee_denied_even_with_injected_purchasing_grants(actor, grants):
    employee = replace(actor, principal=AuthenticatedPrincipal(
        Role.EMPLOYEE, 10, 2, grants,
    ))
    data = {**valid_receiving(), "role": "Owner", "permissions": [PURCHASES_MANAGE]}
    assert_denied(lambda: prepare(employee, data))


@pytest.mark.parametrize("bad_actor", [None, {}, "Owner", object(), 23])
def test_missing_or_malformed_actor(bad_actor):
    assert_denied(lambda: prepare(bad_actor, valid_receiving()))


@pytest.mark.parametrize("user_id", [None, True, False, 0, -1, "23", 23.0])
def test_invalid_authenticated_user_id(actor, user_id):
    assert_denied(lambda: prepare(replace(actor, user_id=user_id), valid_receiving()))


@pytest.mark.parametrize("principal", [
    None, {}, "Owner",
    AuthenticatedPrincipal("Owner", 10, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, True, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 0, None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, "10", None, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 10, 2, frozenset({PURCHASES_MANAGE})),
    AuthenticatedPrincipal(Role.OWNER, 10, None, [PURCHASES_MANAGE]),
    AuthenticatedPrincipal(Role.OWNER, 10, None, frozenset({42})),
    AuthenticatedPrincipal(Role.BRANCH_MANAGER, 10, None, frozenset({PURCHASES_MANAGE_AUTHORIZED})),
])
def test_malformed_principal_fails_closed(actor, principal):
    assert_denied(lambda: prepare(replace(actor, principal=principal), valid_receiving()))


@pytest.mark.parametrize("scope", [
    None, {}, {"business_id": 10, "branch_id": 2},
    ResourceScope(99, 2), ResourceScope(None, 2), ResourceScope(True, 2),
    ResourceScope(0, 2), ResourceScope("10", 2),
    *(ResourceScope(10, value) for value in (None, True, False, 0, -1, "2", 2.0)),
])
def test_invalid_or_foreign_destination_scope(actor, scope):
    assert_denied(lambda: prepare(actor, valid_receiving(), branch_scope=scope))


@pytest.mark.parametrize("count", [None, True, False, -1, 1.5, "1", {}, Decimal("1")])
def test_invalid_trusted_branch_count_preserves_onboarding_error(actor, count):
    with pytest.raises(ValueError, match="^branch_count must be a non-negative integer.$"):
        prepare(actor, valid_receiving(), business_branch_count=count)


@pytest.mark.parametrize("missing", ["actor", "branch_scope", "business_branch_count"])
def test_trusted_context_arguments_are_required(actor, missing):
    context = {
        "actor": actor, "branch_scope": ResourceScope(10, 2), "business_branch_count": 1,
    }
    del context[missing]
    with pytest.raises(TypeError, match=missing):
        prepare_purchase_receiving_input(valid_receiving(), **context)


def test_trusted_context_cannot_be_passed_positionally(actor):
    with pytest.raises(TypeError):
        prepare_purchase_receiving_input(valid_receiving(), actor, ResourceScope(10, 2), 1)


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_onboarding_denial_cannot_be_overridden_by_request_count(actor, manager, role):
    current = actor if role is Role.OWNER else manager
    data = {**valid_receiving(), "business_branch_count": 1, "branch_count": 1}
    assert_denied(lambda: prepare(current, data, business_branch_count=0))


def test_authorization_precedes_input_validation(actor, manager, monkeypatch):
    validator = Mock(side_effect=AssertionError("Unauthorized input validated"))
    monkeypatch.setattr(receiving_service, "validate_purchase_receiving_input", validator)
    data = {"private-field": "untrusted-secret"}
    denied = replace(actor, principal=replace(actor.principal, permissions=frozenset()))
    assert_denied(lambda: prepare(None, data))
    assert_denied(lambda: prepare(denied, data))
    assert_denied(lambda: prepare(actor, data, branch_scope=ResourceScope(99, 2)))
    assert_denied(lambda: prepare(manager, data, branch_scope=ResourceScope(10, 7)))
    assert_denied(lambda: prepare(actor, data, branch_scope=ResourceScope(10, None)))
    assert_denied(lambda: prepare(actor, data, business_branch_count=0))
    validator.assert_not_called()


@pytest.mark.parametrize("data", [None, [], (), "receiving", 123, True, {}])
def test_malformed_bodies_reuse_existing_validator(actor, data):
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)


@pytest.mark.parametrize("field", ["purchase_order_id", "items"])
def test_missing_root_fields(actor, field):
    data = valid_receiving()
    del data[field]
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)
    assert field in prepare(actor, data).errors


@pytest.mark.parametrize("location,field,error_key", [
    ("root", "purchase_order_id", "purchase_order_id"),
    ("item", "purchase_order_line_id", "items[0].purchase_order_line_id"),
])
@pytest.mark.parametrize("value", [
    None, True, False, 0, -1, "25", 25.0, Decimal("25"), IntegerSubclass(25), [], {},
])
def test_invalid_ids_remain_structural_errors(actor, location, field, error_key, value):
    data = valid_receiving()
    target = data if location == "root" else data["items"][0]
    target[field] = value
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)
    assert error_key in prepare(actor, data).errors


@pytest.mark.parametrize("identifier", [1, 25, 2**100])
def test_positive_builtin_ids_have_no_invented_database_limits(actor, identifier):
    data = valid_receiving()
    data["purchase_order_id"] = identifier
    data["items"][0]["purchase_order_line_id"] = identifier
    assert not prepare(actor, data).errors


@pytest.mark.parametrize("items", [None, [], {}, "items", 12, True, (), ({},)])
def test_empty_or_malformed_items(actor, items):
    data = {**valid_receiving(), "items": items}
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)


@pytest.mark.parametrize("item", [None, [], (), "item", 1, True])
def test_malformed_item(actor, item):
    data = {**valid_receiving(), "items": [item]}
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)


@pytest.mark.parametrize("field", ["purchase_order_line_id", "received_quantity"])
def test_missing_item_fields(actor, field):
    data = valid_receiving()
    del data["items"][0][field]
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)
    assert f"items[0].{field}" in prepare(actor, data).errors


@pytest.mark.parametrize("quantity", [
    None, True, False, -1, "3", "0", 3.0, 0.0, 1.5,
    Decimal("3"), IntegerSubclass(3), [], {}, float("nan"), float("inf"),
])
def test_invalid_quantities_preserve_types_and_errors(actor, quantity):
    data = valid_receiving()
    data["items"][0]["received_quantity"] = quantity
    assert dict(prepare(actor, data).errors) == validate_purchase_receiving_input(data)
    assert type(data["items"][0]["received_quantity"]) is type(quantity)


@pytest.mark.parametrize("quantity", [0, 1, 3, 2**100])
def test_zero_and_positive_quantities_pass_structure_only(actor, quantity):
    data = valid_receiving()
    data["items"][0]["received_quantity"] = quantity
    assert vars(prepare(actor, data)) == {"errors": {}}


def test_duplicate_lines_remain_unaggregated_without_receiving_policy(actor):
    data = valid_receiving()
    data["items"].append({"purchase_order_line_id": 25, "received_quantity": 0})
    original = deepcopy(data)
    assert not prepare(actor, data).errors
    assert data == original
    assert [item["received_quantity"] for item in data["items"]] == [3, 0]


@pytest.mark.parametrize("field", [
    "private-field-name", "actor", "actor_id", "role", "business_id", "branch_id",
    "user_id", "permissions", "grants", "branch_scope", "business_branch_count",
    "branch_count", "authorized", "status", "received_at", "receipt_id",
    "reference", "idempotency_key", "transaction_id", "committed", "stock_quantity",
    "disposition", "product_variant_id", "remaining_quantity",
])
@pytest.mark.parametrize("location", ["root", "item"])
def test_unknown_fields_and_forged_metadata_are_not_reflected(actor, field, location):
    data = valid_receiving()
    target = data if location == "root" else data["items"][0]
    target[field] = "untrusted-secret"
    error_key = "fields" if location == "root" else "items[0].fields"
    result = prepare(actor, data)
    assert dict(result.errors) == {error_key: "Unknown fields are not allowed."}
    assert field not in repr(result)
    assert "untrusted-secret" not in repr(result)


@pytest.mark.parametrize("items", [None, [], {}, "items"])
def test_unknown_root_metadata_is_rejected_even_with_invalid_items(actor, items):
    data = {"purchase_order_id": 10, "items": items, "business_id": 999}
    assert dict(prepare(actor, data).errors) == {
        "items": "At least one receiving item is required.",
        "fields": "Unknown fields are not allowed.",
    }


@pytest.mark.parametrize("case", ["valid", "invalid", "unknown", "denied"])
def test_caller_input_and_nested_objects_are_unchanged(actor, case):
    data = valid_receiving()
    data["items"].append({"purchase_order_line_id": 25, "received_quantity": 0})
    if case == "invalid":
        data["items"][0]["received_quantity"] = "3"
    elif case == "unknown":
        data["permissions"] = {"nested": ["untrusted-secret"]}
        data["items"][0]["status"] = "untrusted-secret"
    elif case == "denied":
        actor = replace(actor, principal=replace(actor.principal, permissions=frozenset()))
    original = deepcopy(data)
    original_items, original_line = data["items"], data["items"][0]
    if case == "denied":
        assert_denied(lambda: prepare(actor, data))
    else:
        prepare(actor, data)
    assert data == original
    assert data["items"] is original_items
    assert data["items"][0] is original_line


def test_structural_and_unknown_errors_are_deterministic_and_non_disclosing(actor):
    data = {"purchase_order_id": False, "role": "untrusted-secret", "items": [
        None, {"purchase_order_line_id": "25", "received_quantity": -1,
               "business_id": "untrusted-secret"},
    ]}
    expected = {
        "purchase_order_id": "A valid purchase order ID is required.",
        "items[0]": "Item must be a dictionary.",
        "items[1].purchase_order_line_id": "A valid purchase order line ID is required.",
        "items[1].received_quantity": "Received quantity must be a non-negative integer.",
        "fields": "Unknown fields are not allowed.",
        "items[1].fields": "Unknown fields are not allowed.",
    }
    first, second = prepare(actor, data), prepare(actor, data)
    assert list(first.errors.items()) == list(second.errors.items()) == list(expected.items())
    assert first.errors is not second.errors
    assert "untrusted-secret" not in repr(first)


@pytest.mark.parametrize("data", [None, {}, valid_receiving()])
def test_preparation_and_errors_are_immutable(actor, data):
    result = prepare(actor, data)
    with pytest.raises(FrozenInstanceError):
        result.errors = {}
    with pytest.raises(TypeError):
        result.errors["injected"] = "error"
    with pytest.raises(TypeError):
        del result.errors["purchase_order_id"]


def test_preparation_copies_error_mapping():
    source = {"purchase_order_id": "A valid purchase order ID is required."}
    result = PurchaseReceivingPreparation(source)
    source.clear()
    assert dict(result.errors) == {
        "purchase_order_id": "A valid purchase order ID is required.",
    }


def test_service_delegates_once_without_normalizing_input(actor, monkeypatch):
    data = valid_receiving()
    seen = []
    validator_errors = {"probe": "validation error"}

    def validator(received):
        assert received is data
        seen.append(deepcopy(received))
        return validator_errors

    monkeypatch.setattr(receiving_service, "validate_purchase_receiving_input", validator)
    result = prepare(actor, data)
    assert seen == [data]
    validator_errors.clear()
    assert dict(result.errors) == {"probe": "validation error"}


def test_empty_errors_do_not_verify_order_scope_or_generate_receipts(actor, monkeypatch):
    generated = Mock(side_effect=AssertionError("Identity or timestamp generation attempted"))
    for module, names in (
        (secrets, ("token_hex", "token_urlsafe", "token_bytes")),
        (uuid, ("uuid4",)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, generated)

    class NoClock:
        now = generated
        utcnow = generated

    monkeypatch.setattr(receiving_service, "datetime", NoClock, raising=False)
    data = {
        "purchase_order_id": 999999999,
        "items": [{"purchase_order_line_id": 888888888, "received_quantity": 2**100}],
    }
    # No records are supplied or looked up. Either context passing here proves
    # only preliminary scope checks, never the requested PO's actual destination.
    for destination in (2, 7):
        first = prepare(actor, data, branch_scope=ResourceScope(10, destination))
        replay = prepare(actor, data, branch_scope=ResourceScope(10, destination))
        assert vars(first) == vars(replay) == {"errors": {}}
        assert first.errors is not replay.errors
    generated.assert_not_called()


def test_shared_allowlists_are_immutable_and_preserve_standalone_validator():
    assert type(ALLOWED_FIELDS) is frozenset
    assert type(ALLOWED_ITEM_FIELDS) is frozenset
    assert ALLOWED_FIELDS == {"purchase_order_id", "items"}
    assert ALLOWED_ITEM_FIELDS == {"purchase_order_line_id", "received_quantity"}
    data = valid_receiving()
    data["role"] = "Owner"
    data["items"][0]["status"] = "client-status"
    assert validate_purchase_receiving_input(data) == {}


@pytest.mark.parametrize("change", ["grants", "branch", "inactive", "version", "expired"])
def test_resolved_sessions_reflect_current_identity_before_preparation(manager, change):
    user = TrustedSessionUser(23, 0, True, False, manager.principal)
    current, clock = [user], [datetime(2026, 11, 17, tzinfo=timezone.utc)]
    sessions = SessionManager(
        SessionStore(), lambda user_id: current[0] if user_id == 23 else None,
        clock=lambda: clock[0],
    )
    issued = sessions.create_session(user)
    assert not prepare(sessions.resolve_session(issued.session_token), valid_receiving()).errors
    if change == "grants":
        current[0] = replace(user, principal=replace(user.principal, permissions=frozenset()))
    elif change == "branch":
        current[0] = replace(user, principal=replace(user.principal, branch_id=7))
    elif change == "inactive":
        current[0] = replace(user, is_active=False)
    elif change == "version":
        current[0] = replace(user, access_version=1)
    else:
        clock[0] += timedelta(days=1)
    assert_denied(lambda: prepare(sessions.resolve_session(issued.session_token), valid_receiving()))
