"""Internal preparation tests, not production API integration."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import socket
import sqlite3
from unittest.mock import Mock
import pytest
from sqlalchemy.orm import Session
from permissions import AuthenticatedPrincipal, ResourceScope, Role, SUPPLIERS_MANAGE
from session_auth import AuthenticatedSession
from branchwise.suppliers.supplier_service import prepare_supplier_create
from branchwise.suppliers.supplier_validation import validate_supplier_input


@pytest.fixture
def actor():
    return AuthenticatedSession(23, 0, AuthenticatedPrincipal(
        Role.OWNER, 10, None, frozenset({SUPPLIERS_MANAGE})),
        datetime(2030, 1, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64)


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    blocked = Mock(side_effect=AssertionError("External operation attempted"))
    for target, names in ((socket, ("create_connection", "getaddrinfo")),
                          (socket.socket, ("connect", "connect_ex", "sendto")),
                          (sqlite3, ("connect",)),
                          (Session, ("add", "execute", "flush", "commit", "delete"))):
        for name in names:
            monkeypatch.setattr(target, name, blocked)
    yield
    blocked.assert_not_called()


def prepare(actor, data, scope=ResourceScope(10, None)):
    return prepare_supplier_create(data, actor=actor, scope=scope)


def test_authorized_owner_without_persistence_claim(actor):
    result = prepare(actor, {"name": "Supplier"})
    assert dict(result.errors) == {}
    assert set(vars(result)) == {"errors"}


@pytest.mark.parametrize("actor", [None, {}, "Owner"])
def test_missing_authentication(actor):
    with pytest.raises(PermissionError):
        prepare(actor, {})


@pytest.mark.parametrize("change", [
    {"permissions": frozenset()}, {"business_id": None}, {"business_id": False},
    {"role": "Owner"},
    {"role": Role.BRANCH_MANAGER, "branch_id": 2},
    {"role": Role.EMPLOYEE, "branch_id": 2},
])
def test_unauthorized_principal(actor, change):
    with pytest.raises(PermissionError):
        prepare(replace(actor, principal=replace(actor.principal, **change)), {})


@pytest.mark.parametrize("scope", [None, ResourceScope(99, None), ResourceScope(0, None)])
def test_wrong_business_scope(actor, scope):
    with pytest.raises(PermissionError):
        prepare(actor, {"name": "Supplier"}, scope)


@pytest.mark.parametrize("field", ["business_id", "role", "permissions", "user_id", "extra"])
def test_forged_metadata_and_unknown_fields(actor, field):
    result = prepare(actor, {"name": "Supplier", field: "secret"})
    assert dict(result.errors) == {"body": "This field is not allowed."}
    assert "secret" not in repr(result)


@pytest.mark.parametrize("data", [None, [], {}, {"name": " "}, {"name": False},
    *({"name": "Supplier", field: 42} for field in
      ("contact_person", "phone", "email", "address", "notes")),
    {"name": "Supplier", "email": "invalid"}])
def test_validator_errors_and_input_immutability(actor, data):
    original = deepcopy(data)
    assert dict(prepare(actor, data).errors) == validate_supplier_input(data)
    assert data == original


@pytest.mark.parametrize("value", [None, "", " \t "])
def test_optional_values_preserved(actor, value):
    data = {"name": " Supplier ", **{field: value for field in
        ("contact_person", "phone", "email", "address", "notes")}}
    original = deepcopy(data)
    assert not prepare(actor, data).errors
    assert data == original


def test_invalid_user_identity(actor):
    with pytest.raises(PermissionError):
        prepare(replace(actor, user_id=False), {"name": "Supplier"})
