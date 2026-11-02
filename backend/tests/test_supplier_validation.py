"""Supplier field checks without a database, authentication, or API routes."""

from copy import deepcopy
import socket
import sqlite3
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from supplier_validation import validate_supplier_input


OPTIONAL_FIELDS = ("contact_person", "phone", "email", "address", "notes")


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Fail if field validation attempts networking or a database connection."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    monkeypatch.setattr(socket.socket, "connect", attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)
    fake_db = ModuleType("db")
    fake_db.get_db_connection = attempted_access
    monkeypatch.setitem(sys.modules, "db", fake_db)

    yield attempted_access

    attempted_access.assert_not_called()


@pytest.mark.parametrize("name", ["Main Supplier", "المورد الرئيسي", "  Supplier  "])
def test_name_is_the_only_required_field(name):
    assert validate_supplier_input({"name": name}) == {}


def test_all_approved_fields_can_be_supplied():
    supplier = {
        "name": "Main Supplier",
        "contact_person": "Omar Ali",
        "phone": "+962 6 555 0100",
        "email": "orders@example.com",
        "address": "Amman",
        "notes": "Contact in the morning.",
    }

    assert validate_supplier_input(supplier) == {}


@pytest.mark.parametrize("data", [None, [], "Supplier", 42])
def test_input_must_be_a_dictionary(data):
    assert validate_supplier_input(data) == {
        "body": "Supplier input must be a dictionary."
    }


@pytest.mark.parametrize(
    "data",
    [{}, {"contact_person": "Omar"}, {"name": None}, {"name": ""}, {"name": " \t\n "}],
)
def test_missing_or_blank_name_is_rejected(data):
    assert set(validate_supplier_input(data)) == {"name"}


@pytest.mark.parametrize("name", [0, False, [], {}])
def test_name_must_be_text(name):
    assert set(validate_supplier_input({"name": name})) == {"name"}


@pytest.mark.parametrize("field", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", [None, "", " \t "])
def test_optional_fields_can_have_no_supplied_value(field, value):
    assert validate_supplier_input({"name": "Supplier", field: value}) == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("contact_person", 123),
        ("phone", 96265550100),
        ("email", False),
        ("address", ["Amman"]),
        ("notes", {"message": "Call first"}),
    ],
)
def test_populated_optional_fields_must_be_text(field, value):
    assert set(validate_supplier_input({"name": "Supplier", field: value})) == {field}


@pytest.mark.parametrize(
    "email",
    [
        "not-an-email",
        "@example.com",
        "orders@",
        "orders@example",
        "orders@@example.com",
        "order person@example.com",
        "orders@exam ple.com",
    ],
)
def test_malformed_email_is_rejected(email):
    assert set(validate_supplier_input({"name": "Supplier", "email": email})) == {
        "email"
    }


@pytest.mark.parametrize(
    "email", ["orders@example.com", "orders+branch@shop.example", " orders@example.com "]
)
def test_basic_email_syntax_is_accepted(email):
    assert validate_supplier_input({"name": "Supplier", "email": email}) == {}


def test_phone_does_not_apply_unapproved_format_rules():
    assert validate_supplier_input({"name": "Supplier", "phone": "extension 4"}) == {}


def test_multiple_field_errors_are_returned_together():
    supplier = {"name": " ", "phone": 123, "email": "invalid"}

    assert set(validate_supplier_input(supplier)) == {"name", "phone", "email"}


@pytest.mark.parametrize(
    "supplier",
    [
        {"name": "  Supplier  ", "email": " orders@example.com ", "notes": None},
        {"name": " ", "email": "invalid", "address": ["Amman"]},
    ],
)
def test_validation_does_not_modify_input(supplier):
    original = deepcopy(supplier)

    validate_supplier_input(supplier)

    assert supplier == original
