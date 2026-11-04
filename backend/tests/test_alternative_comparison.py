"""BE-42 checks using explicitly synthetic, in-memory supplier-cost records."""

import builtins
from copy import deepcopy
from decimal import Decimal, localcontext
import io
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import alternative_comparison
from alternative_comparison import prepare_supplier_cost_comparison


def observation(**changes):
    """Return a synthetic fixture, never a real supplier quotation or price."""
    record = {
        "business_id": 1,
        "product_variant_id": 3,
        "supplier_id": 7,
        "source_id": "purchase-test-1",
        "source_type": "historical_purchase",
        "cost": "12.3400",
        "currency": "JOD",
        "quantity_unit": "each",
        "cost_basis": "unit_cost",
        "provenance": {"reference": "synthetic purchase", "date": "2026-11-01"},
    }
    record.update(changes)
    return record


def prepare(records, **changes):
    context = {
        "business_id": 1,
        "product_variant_id": 3,
        "currency": "JOD",
        "quantity_unit": "each",
        "cost_basis": "unit_cost",
    }
    context.update(changes)
    return prepare_supplier_cost_comparison(records, **context)


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Catch forbidden operations even if a helper accidentally swallows them."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)

    original_import = builtins.__import__
    forbidden_modules = {
        "db", "mysql", "pymysql", "sqlalchemy", "flask", "google",
        "gemini_provider", "ai_client", "ai_service",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in forbidden_modules:
            return attempted_access(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted_access
    attempted_access.assert_not_called()


def test_valid_cost_preserves_identity_source_and_context():
    row = observation(branch_id="branch-test-2")

    result = prepare([row])

    assert result == {
        "business_id": 1,
        "product_variant_id": 3,
        "currency": "JOD",
        "quantity_unit": "each",
        "cost_basis": "unit_cost",
        "observations": [{**row, "cost": Decimal("12.3400")}],
        "branch_transfer_assessed": False,
    }
    assert type(result["observations"][0]["cost"]) is Decimal


def test_multiple_suppliers_and_repeated_supplier_observations_keep_input_order():
    records = [
        observation(supplier_id=9, cost="20.00"),
        observation(supplier_id=7, source_id="quote-test-1", source_type="quote", cost="2.00"),
        observation(supplier_id=9, source_id="purchase-test-2", cost="5.00"),
    ]

    rows = prepare(records)["observations"]

    assert [row["supplier_id"] for row in rows] == [9, 7, 9]
    assert [row["source_id"] for row in rows] == ["purchase-test-1", "quote-test-1", "purchase-test-2"]
    assert [row["cost"] for row in rows] == [Decimal("20.00"), Decimal("2.00"), Decimal("5.00")]
    assert [row["source_type"] for row in rows] == ["historical_purchase", "quote", "historical_purchase"]


def test_repeated_source_identity_is_not_silently_deduplicated():
    row = observation()
    assert len(prepare([row, row])["observations"]) == 2


def test_business_mismatch_with_repeated_supplier_id_rejects_entire_comparison():
    records = [observation(supplier_id=7), observation(business_id=99, supplier_id=7)]
    original = deepcopy(records)

    with pytest.raises(ValueError) as error:
        prepare(records)

    assert records == original
    assert "99" not in str(error.value)


@pytest.mark.parametrize("field", ["business_id", "product_variant_id"])
@pytest.mark.parametrize("value", [99, "1", "3", "private-other-scope"])
def test_scope_mismatch_never_returns_foreign_rows_or_values(field, value):
    with pytest.raises(ValueError) as error:
        prepare([observation(**{field: value})])
    assert "private-other-scope" not in str(error.value)


@pytest.mark.parametrize("field", ["business_id", "product_variant_id"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, Decimal("1"), "", "  ", [], {}, b"1"])
def test_requested_identifiers_have_explicit_types(field, value):
    with pytest.raises(ValueError):
        prepare([], **{field: value})


@pytest.mark.parametrize(
    "field", ["business_id", "product_variant_id", "currency", "quantity_unit", "cost_basis"]
)
def test_comparison_scope_and_context_arguments_cannot_be_omitted(field):
    context = {
        "business_id": 1,
        "product_variant_id": 3,
        "currency": "JOD",
        "quantity_unit": "each",
        "cost_basis": "unit_cost",
    }
    del context[field]
    with pytest.raises(TypeError):
        prepare_supplier_cost_comparison([], **context)


@pytest.mark.parametrize("field", ["business_id", "product_variant_id", "supplier_id", "source_id", "branch_id"])
@pytest.mark.parametrize("value", [None, True, 0, -1, 1.0, "", []])
def test_observation_identifiers_are_validated(field, value):
    with pytest.raises(ValueError):
        prepare([observation(**{field: value})])


def test_string_scope_identifiers_and_integer_source_identifiers_are_preserved():
    row = observation(business_id="business-test", product_variant_id="variant-test", supplier_id="supplier-test", source_id=4)
    result = prepare([row], business_id="business-test", product_variant_id="variant-test")
    assert result["business_id"] == "business-test"
    assert result["observations"][0]["source_id"] == 4
    assert result["observations"][0]["supplier_id"] == "supplier-test"


def test_missing_cost_and_explicit_zero_remain_different():
    rows = prepare([observation(cost=None), observation(cost="0.000"), observation(cost=Decimal("0"))])["observations"]
    assert rows[0]["cost"] is None
    assert rows[1]["cost"].as_tuple() == Decimal("0.000").as_tuple()
    assert rows[2]["cost"].as_tuple() == Decimal("0").as_tuple()


@pytest.mark.parametrize("cost", ["0.1", "00012.3400", "123456789012345678901234567890.123456789", Decimal("1E-40"), Decimal("-0.00")])
def test_exact_cost_and_precision_survive_small_decimal_context(cost):
    expected = Decimal(cost)
    with localcontext() as context:
        context.prec = 2
        actual = prepare([observation(cost=cost)])["observations"][0]["cost"]
    assert actual.as_tuple() == expected.as_tuple()


@pytest.mark.parametrize(
    "cost",
    [
        True, False, 0, 1, -1, 0.1, float("nan"), float("inf"), [], {}, b"1.0",
        "", "private-invalid-cost", "-1.00", "+1.00", " 1.00", "1.00 ",
        "1e2", "1_000", ".50", "1.", "NaN", "Infinity", "١.٢",
        Decimal("-0.01"), Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
    ],
)
def test_invalid_negative_or_binary_floating_point_costs_raise_safe_errors(cost):
    with pytest.raises(ValueError) as error:
        prepare([observation(cost=cost)])
    assert "private-invalid-cost" not in str(error.value)


@pytest.mark.parametrize("field", ["currency", "quantity_unit", "cost_basis"])
@pytest.mark.parametrize("value", [None, True, 1, "", " \t ", []])
def test_explicit_comparison_context_is_required_even_for_empty_data(field, value):
    with pytest.raises(ValueError):
        prepare([], **{field: value})


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("currency", "jod"), ("quantity_unit", "box"), ("cost_basis", "line_total")])
@pytest.mark.parametrize("cost", [None, "1.00"])
def test_incompatible_context_is_rejected_without_conversion(field, value, cost):
    with pytest.raises(ValueError):
        prepare([observation(**{field: value, "cost": cost})])


@pytest.mark.parametrize("field", ["currency", "quantity_unit", "cost_basis"])
@pytest.mark.parametrize("value", [None, True, 1, "", [], {}])
def test_observation_context_values_are_validated(field, value):
    with pytest.raises(ValueError):
        prepare([observation(**{field: value})])


@pytest.mark.parametrize("source_type", [None, True, 1, "", "purchase", "Historical_Purchase", "delivery", []])
def test_only_documented_source_types_are_accepted(source_type):
    with pytest.raises(ValueError):
        prepare([observation(source_type=source_type)])


@pytest.mark.parametrize("provenance", [None, [], "source", {}, {"": "source"}, {" ": "source"}, {1: "source"}, {"reference": None}, {"reference": ""}, {"reference": " "}, {"reference": 3}, {"reference": {"nested": "value"}}])
def test_missing_or_invalid_provenance_is_rejected(provenance):
    with pytest.raises(ValueError):
        prepare([observation(provenance=provenance)])


def test_supplied_provenance_is_opaque_preserved_information():
    provenance = {"reference": "  supplied source label  ", "note": "Historical, not a current quotation"}
    result = prepare([observation(provenance=provenance)])
    assert result["observations"][0]["provenance"] == provenance


@pytest.mark.parametrize("field", list(observation()))
def test_every_documented_observation_field_is_required(field):
    row = observation()
    del row[field]
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("records", [None, {}, "records", 1, True, {1}, (observation(), None)])
def test_invalid_collection_or_observation_is_rejected(records):
    with pytest.raises(ValueError):
        prepare(records)


def test_tuple_observations_are_supported():
    assert len(prepare((observation(),))["observations"]) == 1


def test_empty_input_has_no_observations_and_transfer_assessment_remains_unknown():
    result = prepare([])
    assert result["observations"] == []
    assert result["branch_transfer_assessed"] is False


def test_results_are_deterministic_and_inputs_stay_unchanged():
    records = [observation(), observation(source_id="quote-test", source_type="quote", cost=None)]
    original = deepcopy(records)
    assert prepare(records) == prepare(records)
    assert records == original


def test_returned_rows_and_provenance_do_not_alias_inputs():
    records = [observation()]
    original = deepcopy(records)
    result = prepare(records)
    result["observations"][0]["supplier_id"] = "changed"
    result["observations"][0]["provenance"]["reference"] = "changed"
    result["observations"].append({})
    assert records == original


def test_no_ranking_totals_authorization_or_transfer_decisions_are_invented():
    row = observation(best_supplier=True, rating=5, available_qty=200, purchase_total="999", authorized=True)
    result = prepare([row])
    assert set(result) == {"business_id", "product_variant_id", "currency", "quantity_unit", "cost_basis", "observations", "branch_transfer_assessed"}
    assert set(result["observations"][0]) == set(observation())
    assert result["branch_transfer_assessed"] is False


def test_helper_has_no_filesystem_or_external_service_access(monkeypatch, blocked_external_services):
    attempted_file_access = Mock(side_effect=AssertionError("File access attempted"))
    records = [observation()]
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", attempted_file_access)
        guard.setattr(io, "open", attempted_file_access)
        guard.setattr(os, "open", attempted_file_access)
        guard.setattr(Path, "open", attempted_file_access)
        result = prepare(records)
    assert result["observations"][0]["cost"] == Decimal("12.3400")
    attempted_file_access.assert_not_called()
    blocked_external_services.assert_not_called()


def test_module_initialization_and_call_need_no_filesystem_or_services(monkeypatch, blocked_external_services):
    source_path = Path(alternative_comparison.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted_file_access = Mock(side_effect=AssertionError("File access attempted"))
    namespace = {"__name__": "comparison_import_probe"}
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", attempted_file_access)
        guard.setattr(io, "open", attempted_file_access)
        guard.setattr(os, "open", attempted_file_access)
        guard.setattr(Path, "open", attempted_file_access)
        exec(module_code, namespace)
        result = namespace["prepare_supplier_cost_comparison"](
            [], business_id=1, product_variant_id=3,
            currency="JOD", quantity_unit="each", cost_basis="unit_cost",
        )
    assert result["observations"] == []
    assert result["branch_transfer_assessed"] is False
    attempted_file_access.assert_not_called()
    blocked_external_services.assert_not_called()
