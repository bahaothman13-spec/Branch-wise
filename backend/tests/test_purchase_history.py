"""BE-31 tests using synthetic, already-authorized purchase-order observations."""

import builtins
from copy import deepcopy
from decimal import Decimal, Inexact, Rounded, localcontext
import io
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import backend.purchase_history as purchase_history
from backend.purchase_history import prepare_purchase_history


class IntegerSubclass(int):
    pass


def observation(**changes):
    return {
        "business_id": 1,
        "branch_id": 2,
        "supplier_id": 3,
        "purchase_order_id": 10,
        "purchase_order_reference": "PO-TEST-10",
        **changes,
    }


def prepare(records, **changes):
    scope = {"business_id": 1, "branch_id": 2}
    scope.update(changes)
    return prepare_purchase_history(records, **scope)


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Detect attempted service access even when a helper catches the error."""
    attempted = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)
    monkeypatch.setattr(sqlite3, "connect", attempted)

    original_import = builtins.__import__
    forbidden = {
        "app", "db", "mysql", "pymysql", "sqlalchemy", "flask", "google",
        "gemini_provider", "ai_client", "ai_service", "replenishment",
        "supplier_validation", "purchase_order_validation",
        "purchase_receiving_validation",
    }

    def guarded_import(name, *args, **kwargs):
        if forbidden.intersection(name.split(".")):
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted
    attempted.assert_not_called()


def test_valid_purchase_history():
    records = [observation()]

    assert prepare(records) == records


def test_multiple_purchase_records_and_tuple_input():
    records = (
        observation(),
        observation(supplier_id=4, purchase_order_id=11,
                    purchase_order_reference="PO-TEST-11"),
    )

    assert prepare(records) == list(records)


def test_supplier_filter_selects_only_exact_matches_in_supplied_order():
    records = [
        observation(purchase_order_id=12),
        observation(supplier_id=4, purchase_order_id=11),
        observation(purchase_order_id=10),
    ]

    assert prepare(records, supplier_id=3) == [records[0], records[2]]
    assert prepare(records, supplier_id=4) == [records[1]]


def test_no_matching_supplier_returns_no_invented_records():
    assert prepare([observation()], supplier_id=99) == []


@pytest.mark.parametrize("records", [[], ()])
@pytest.mark.parametrize("supplier_id", [None, 3])
def test_empty_records_return_an_empty_list(records, supplier_id):
    assert prepare(records, supplier_id=supplier_id) == []


@pytest.mark.parametrize("field", ["business_id", "branch_id"])
@pytest.mark.parametrize("value", [
    None, 0, -1, True, False, "1", 1.0, Decimal("1"), IntegerSubclass(1), [], {},
])
def test_selected_scope_requires_positive_builtin_integers_even_when_empty(field, value):
    with pytest.raises(ValueError):
        prepare([], **{field: value})


@pytest.mark.parametrize("value", [
    0, -1, True, False, "3", 3.0, Decimal("3"), IntegerSubclass(3), [], {},
])
def test_supplier_filter_rejects_invalid_identifiers_without_coercion(value):
    with pytest.raises(ValueError):
        prepare([], supplier_id=value)


@pytest.mark.parametrize("field", [
    "business_id", "branch_id", "supplier_id", "purchase_order_id",
])
@pytest.mark.parametrize("value", [
    None, 0, -1, True, False, "3", 3.0, Decimal("3"), IntegerSubclass(3), [], {},
])
def test_every_record_identifier_requires_a_positive_builtin_integer(field, value):
    record = observation(**{field: value})
    original = deepcopy(record)

    with pytest.raises(ValueError):
        prepare([record])

    assert record == original
    assert type(record[field]) is type(value)


def test_large_builtin_integer_identifiers_are_preserved():
    identifier = 2**100
    record = observation(business_id=identifier, branch_id=identifier,
                         supplier_id=identifier, purchase_order_id=identifier)

    assert prepare([record], business_id=identifier, branch_id=identifier,
                   supplier_id=identifier) == [record]


@pytest.mark.parametrize("field", [
    "business_id", "branch_id", "supplier_id", "purchase_order_id",
    "purchase_order_reference",
])
def test_required_record_fields_cannot_be_missing(field):
    record = observation()
    del record[field]

    with pytest.raises(ValueError):
        prepare([record])


@pytest.mark.parametrize("reference", [
    None, "", " \t\n", True, 10, 10.0, Decimal("10"), [], {},
])
def test_purchase_order_reference_must_be_nonblank_text(reference):
    with pytest.raises(ValueError):
        prepare([observation(purchase_order_reference=reference)])


def test_purchase_order_reference_is_preserved_without_normalization():
    record = observation(purchase_order_reference="  PO/Test-001  ")

    assert prepare([record])[0]["purchase_order_reference"] == "  PO/Test-001  "


@pytest.mark.parametrize("records", [None, {}, "records", 1, True, iter([])])
def test_invalid_record_containers_are_rejected(records):
    with pytest.raises(ValueError):
        prepare(records)


@pytest.mark.parametrize("record", [None, [], (), "record", 1, True])
def test_each_observation_must_be_a_dictionary(record):
    with pytest.raises(ValueError):
        prepare([record])


@pytest.mark.parametrize("field", ["business_id", "branch_id"])
def test_records_must_match_selected_business_and_branch(field):
    with pytest.raises(ValueError):
        prepare([observation(**{field: 99})])


@pytest.mark.parametrize("field", ["business_id", "branch_id"])
def test_mixed_scope_is_rejected_even_for_an_unselected_supplier(field):
    records = [observation(), observation(supplier_id=4, **{field: 99})]
    original = deepcopy(records)

    with pytest.raises(ValueError):
        prepare(records, supplier_id=3)

    assert records == original


@pytest.mark.parametrize("changes", [
    {"purchase_order_id": False}, {"purchase_order_reference": " "},
])
def test_malformed_unselected_records_are_not_silently_filtered_out(changes):
    records = [observation(), observation(supplier_id=4, **changes)]

    with pytest.raises(ValueError):
        prepare(records, supplier_id=3)


def test_permission_flags_cannot_make_a_foreign_scope_valid():
    with pytest.raises(ValueError):
        prepare([observation(business_id=99, authorized=True)])


def test_record_order_is_preserved_without_sorting_by_reference_or_date():
    records = [
        observation(purchase_order_id=20, purchase_order_reference="Z-test",
                    date="caller-supplied-later-label"),
        observation(purchase_order_id=10, purchase_order_reference="A-test",
                    date="caller-supplied-earlier-label"),
    ]

    assert prepare(records) == records


def test_repeated_records_and_references_remain_repeated():
    record = observation(extra={"labels": ["supplied"]})
    records = [record, record, deepcopy(record)]
    result = prepare(records)

    assert result == records
    assert len(result) == 3
    assert result[0] is not result[1]
    assert result[0]["extra"] is not result[1]["extra"]
    result[0]["extra"]["labels"].append("result-only")
    assert result[1]["extra"]["labels"] == ["supplied"]
    assert record["extra"]["labels"] == ["supplied"]


@pytest.mark.parametrize("amount", [
    Decimal("12.3400"), Decimal("-0.0000"), Decimal("1E1001"),
    Decimal("1E-1001"), Decimal("12345678901234567890.123456789"),
])
def test_decimal_representation_is_unchanged_under_restrictive_context(amount):
    # These extra values are opaque test data, not approved monetary fields.
    record = observation(provided_values={"amount": amount})
    expected = amount.as_tuple()

    with localcontext() as context:
        context.prec = 2
        context.Emax, context.Emin = 2, -2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.clear_flags()
        result = prepare([record])

        assert result[0]["provided_values"]["amount"].as_tuple() == expected
        assert not any(context.flags.values())


def test_inputs_and_nested_provided_values_are_unchanged():
    record = observation(extra={"labels": ["supplied"], "values": (1, [2, 3])})
    records = [record, observation(supplier_id=4)]
    original = deepcopy(records)

    result = prepare(records, supplier_id=3)

    assert records == original
    assert result == [original[0]]
    assert records[0] is record


def test_outputs_are_independent_of_inputs_and_each_other():
    records = [observation(extra={"labels": ["supplied"]})]
    original = deepcopy(records)
    first, second = prepare(records), prepare(records)

    assert first == second == original
    assert first is not second
    assert first[0] is not second[0]
    assert first[0] is not records[0]
    first[0]["extra"]["labels"].append("changed")
    first.append(observation())
    assert records == original
    assert second == original
    assert prepare(records) == original


def test_minimal_history_does_not_invent_dates_totals_or_supplier_details():
    record = observation()
    result = prepare([record])

    assert result == [record]
    assert set(result[0]) == set(record)


def test_supplied_status_dates_and_flags_are_preserved_as_opaque_values():
    record = observation(
        status="uninterpreted-status", authorized=False,
        date="uninterpreted-date", notes={"text": "supplied annotation"},
    )

    assert prepare([record]) == [record]


def test_errors_do_not_disclose_supplied_reference_values():
    private_value = "private-foreign-business"

    with pytest.raises(ValueError) as error:
        prepare([observation(business_id=private_value)])

    assert private_value not in str(error.value)


def test_module_initialization_and_preparation_need_no_files_or_services(
    monkeypatch, blocked_external_services,
):
    source_path = Path(purchase_history.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted = Mock(side_effect=AssertionError("Filesystem access attempted"))
    namespace = {"__name__": "purchase_history_import_probe"}
    records = [observation(extra={"values": [Decimal("1.2300")]})]
    original = deepcopy(records)

    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (io, "open"), (os, "open"), (os, "write"),
            (Path, "open"), (Path, "write_text"), (Path, "write_bytes"),
        ):
            guard.setattr(owner, name, attempted)
        exec(module_code, namespace)
        prepare_history = namespace["prepare_purchase_history"]
        assert prepare_history(records, business_id=1, branch_id=2) == original
        assert prepare_history(records, business_id=1, branch_id=2, supplier_id=99) == []
        assert prepare_history([], business_id=1, branch_id=2) == []
        with pytest.raises(ValueError):
            prepare_history([observation(branch_id=99)], business_id=1, branch_id=2)

    assert records == original
    attempted.assert_not_called()
    blocked_external_services.assert_not_called()
