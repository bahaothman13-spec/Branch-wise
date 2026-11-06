"""BE-49 checks using synthetic, already-calculated branch sales reports."""

import builtins
from copy import deepcopy
from datetime import date
from decimal import Decimal, Inexact, Rounded, localcontext
import io
from itertools import permutations
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import branch_comparison_report
from branch_comparison_report import prepare_branch_sales_comparison
from sales_performance_report import calculate_sales_performance_report


class IntegerSubclass(int):
    pass


class StringSubclass(str):
    pass


def definition_context():
    return {"definition": "synthetic-sales-v1", "day_basis": "caller-attributed-test-days"}


def report(branch_id=2, **changes):
    """Existing BE-47 output shape with synthetic values, never real sales."""
    result = {
        "business_id": 1, "branch_id": branch_id,
        "start_date": "2026-11-01", "end_date": "2026-11-03",
        "currency": "JOD", "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
        "summary": {"transaction_count": 2, "total_quantity": 3,
                    "total_sales_value": Decimal("12.3400")},
        "products": [],
    }
    result.update(changes)
    return result


def entry(branch_id=2, **changes):
    return {"report": report(branch_id, **changes),
            "report_definition_context": definition_context()}


def prepare(entries, **changes):
    context = {"business_id": 1, "report_definition_context": definition_context()}
    context.update(changes)
    return prepare_branch_sales_comparison(entries, **context)


def expected_branch(branch_id=2, **changes):
    result = {
        "branch_id": branch_id,
        "start_date": "2026-11-01", "end_date": "2026-11-03",
        "currency": "JOD", "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
        "summary": {"transaction_count": 2, "total_quantity": 3,
                    "total_sales_value": Decimal("12.3400")},
    }
    result.update(changes)
    return result


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    attempted = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)
    monkeypatch.setattr(sqlite3, "connect", attempted)
    original_import = builtins.__import__
    forbidden = {
        "app", "db", "mysql", "pymysql", "sqlalchemy", "flask", "google",
        "gemini_provider", "ai_client", "ai_service", "dashboard_stock_alerts",
        "supplier_validation", "alternative_comparison", "replenishment",
        "sales_performance_report", "inventory_snapshot_report", "report_export",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in forbidden:
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted
    attempted.assert_not_called()


def test_compatible_branches_keep_existing_values_and_context_without_calculation():
    second_summary = {"transaction_count": 5, "total_quantity": -7,
                      "total_sales_value": Decimal("-0.1000")}
    result = prepare([entry(9, summary=second_summary), entry(2)])
    assert result == {
        "business_id": 1, "report_definition_context": definition_context(),
        "branches": [expected_branch(2), expected_branch(9, summary=second_summary)],
    }


def test_actual_be47_outputs_are_accepted_without_adding_fields_to_them():
    reports = []
    for branch_id in (9, 2):
        source = [{
            "business_id": 1, "branch_id": branch_id, "transaction_id": "test-sale",
            "product_id": 3, "date": "2026-11-01", "quantity": 7,
            "sales_value": "0.123400", "currency": "JOD", "quantity_unit": "each",
            "sales_value_basis": "caller-defined-test-basis",
        }]
        reports.append(calculate_sales_performance_report(
            source, business_id=1, branch_id=branch_id,
            start_date="2026-11-01", end_date="2026-11-03",
            currency="JOD", quantity_unit="each", sales_value_basis="caller-defined-test-basis",
        ))
    original = deepcopy(reports)
    result = prepare([{"report": item, "report_definition_context": definition_context()}
                      for item in reports])
    assert [item["branch_id"] for item in result["branches"]] == [2, 9]
    assert all(item["summary"] == reports[0]["summary"] for item in result["branches"])
    assert result["branches"][0]["summary"]["total_sales_value"].as_tuple() == Decimal("0.123400").as_tuple()
    assert reports == original
    assert all("report_definition_context" not in item for item in reports)


def test_branch_identifier_order_is_deterministic_and_keeps_types_distinct():
    rows = [entry("2"), entry(9), entry("1"), entry(2), entry(1)]
    assert [item["branch_id"] for item in prepare(rows)["branches"]] == [1, 2, 9, "1", "2"]


@pytest.mark.parametrize("business_id", [99, "1", True, None])
def test_foreign_or_invalid_business_rejects_the_whole_comparison(business_id):
    with pytest.raises(ValueError):
        prepare([entry(2), entry(3, business_id=business_id, authorized=True)])


def test_string_business_identifier_is_supported_without_coercion():
    result = prepare([entry(business_id="business-test")], business_id="business-test")
    assert result["business_id"] == "business-test"


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "", " \t ", [], {},
                                    Decimal("1"), IntegerSubclass(1), StringSubclass("branch")])
def test_invalid_branch_identifiers_are_not_coerced(value):
    with pytest.raises(ValueError):
        prepare([entry(value)])


@pytest.mark.parametrize("value", [None, True, 0, -1, "", [], 1.0])
def test_requested_business_identifier_is_validated_even_for_empty_input(value):
    with pytest.raises(ValueError):
        prepare([], business_id=value)


@pytest.mark.parametrize("branch_id", [2, "branch-test"])
def test_duplicate_branch_reports_are_rejected_even_when_values_differ(branch_id):
    first, second = entry(branch_id), entry(branch_id)
    second["report"]["summary"]["total_quantity"] = 999
    with pytest.raises(ValueError):
        prepare([first, second])


def test_repeating_the_same_wrapper_is_also_a_duplicate():
    row = entry()
    with pytest.raises(ValueError):
        prepare([row, row])


@pytest.mark.parametrize("rows", [None, {}, "reports", True, 3, iter([]), [None], ["report"]])
def test_invalid_containers_and_wrappers(rows):
    with pytest.raises(ValueError):
        prepare(rows)


@pytest.mark.parametrize("field", ["report", "report_definition_context"])
def test_each_wrapper_field_is_required(field):
    row = entry()
    del row[field]
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("value", [None, [], "report", 2, {}])
def test_missing_or_malformed_report_is_rejected(value):
    row = entry()
    row["report"] = value
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("field", [
    "business_id", "branch_id", "start_date", "end_date", "currency",
    "quantity_unit", "sales_value_basis", "summary",
])
def test_required_existing_be47_report_fields_cannot_be_omitted(field):
    row = entry()
    del row["report"][field]
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("field", ["transaction_count", "total_quantity", "total_sales_value"])
def test_every_existing_summary_value_is_required(field):
    row = entry()
    del row["report"]["summary"][field]
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("value", [None, [], "summary", 2, {}])
def test_summary_must_be_a_populated_dictionary(value):
    with pytest.raises(ValueError):
        prepare([entry(summary=value)])


@pytest.mark.parametrize("field,value", [
    ("start_date", "2026-11-02"), ("end_date", "2026-11-04"),
    ("currency", "USD"), ("currency", "jod"), ("quantity_unit", "box"),
    ("sales_value_basis", "other-test-basis"),
])
def test_each_reporting_context_mismatch_rejects_all_branches(field, value):
    with pytest.raises(ValueError):
        prepare([entry(2), entry(3, **{field: value})])


@pytest.mark.parametrize("field", ["start_date", "end_date"])
@pytest.mark.parametrize("value", [None, True, "2026-11-1", "20261101", "2026-02-30",
                                    "2026-11-01T00:00:00", date(2026, 11, 1)])
def test_date_fields_require_normalized_valid_calendar_dates(field, value):
    with pytest.raises(ValueError):
        prepare([entry(**{field: value})])


def test_reversed_date_interval_is_rejected_even_for_a_single_branch():
    with pytest.raises(ValueError):
        prepare([entry(start_date="2026-11-04")])


@pytest.mark.parametrize("field", ["currency", "quantity_unit", "sales_value_basis"])
@pytest.mark.parametrize("value", [None, True, 1, "", " \t ", []])
def test_context_labels_must_be_explicit_nonblank_text(field, value):
    with pytest.raises(ValueError):
        prepare([entry(**{field: value})])


@pytest.mark.parametrize("context", [
    {"definition": "other-test-definition", "day_basis": "caller-attributed-test-days"},
    {"definition": "synthetic-sales-v1"},
    {**definition_context(), "extra": "different-context"},
])
def test_definition_context_mismatches_are_rejected(context):
    second = entry(3)
    second["report_definition_context"] = context
    with pytest.raises(ValueError):
        prepare([entry(2), second])


@pytest.mark.parametrize("context", [None, {}, [], "definition", {"": "value"},
                                      {"definition": " "}, {1: "value"},
                                      {"definition": None}, {"definition": 1},
                                      {"definition": {"nested": "value"}}])
def test_definition_context_is_explicit_flat_nonblank_text_metadata(context):
    with pytest.raises(ValueError):
        prepare([], report_definition_context=context)
    row = entry()
    row["report_definition_context"] = context
    with pytest.raises(ValueError):
        prepare([row])


def test_definition_matching_ignores_dictionary_insertion_order_and_copies_metadata():
    context = definition_context()
    row = entry()
    row["report_definition_context"] = dict(reversed(list(context.items())))
    original = deepcopy(row)
    result = prepare([row], report_definition_context=context)
    assert result["report_definition_context"] == context
    assert result["report_definition_context"] is not context
    assert result["report_definition_context"] is not row["report_definition_context"]
    result["report_definition_context"]["definition"] = "changed"
    assert context == definition_context()
    assert row == original


@pytest.mark.parametrize("value", [None, True, False, -1, 1.0, "2", Decimal("2"), IntegerSubclass(2)])
def test_transaction_count_requires_a_nonnegative_builtin_integer(value):
    summary = {**report()["summary"], "transaction_count": value}
    with pytest.raises(ValueError):
        prepare([entry(summary=summary)])


@pytest.mark.parametrize("value", [None, True, 1.0, "2", Decimal("2"), [], IntegerSubclass(2)])
def test_total_quantity_requires_a_builtin_integer(value):
    summary = {**report()["summary"], "total_quantity": value}
    with pytest.raises(ValueError):
        prepare([entry(summary=summary)])


@pytest.mark.parametrize("value", [None, True, 1, 0.1, "0.10", [], Decimal("NaN"),
                                    Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_money_requires_an_already_calculated_finite_decimal(value):
    summary = {**report()["summary"], "total_sales_value": value}
    with pytest.raises(ValueError):
        prepare([entry(summary=summary)])


@pytest.mark.parametrize("value", [Decimal("12.3400"), Decimal("-0.0000"), Decimal("1E1001"),
                                    Decimal("123456789012345678901234567890.123456789")])
def test_money_representation_and_large_integer_counts_survive_hostile_decimal_context(value):
    summary = {"transaction_count": 2**1000, "total_quantity": -(2**1000 + 1),
               "total_sales_value": value}
    with localcontext() as context:
        context.prec = 2
        context.Emax, context.Emin = 2, -2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = prepare([entry(summary=summary)])
        actual = result["branches"][0]["summary"]
        assert actual == summary
        assert actual["total_sales_value"].as_tuple() == value.as_tuple()
        assert type(actual["transaction_count"]) is int
        assert type(actual["total_quantity"]) is int
        assert not any(context.flags.values())


@pytest.mark.parametrize("rows", [[], ()])
def test_empty_input_returns_no_branches_and_invents_no_zero_sales(rows):
    assert prepare(rows) == {
        "business_id": 1, "report_definition_context": definition_context(), "branches": [],
    }


def test_single_supplied_zero_summary_is_retained_without_extra_branch_rows():
    summary = {"transaction_count": 0, "total_quantity": 0, "total_sales_value": Decimal("0.000")}
    assert prepare([entry(8, summary=summary)])["branches"] == [expected_branch(8, summary=summary)]


def test_no_numeric_relationship_or_product_reconciliation_is_invented():
    summary = {"transaction_count": 0, "total_quantity": -7, "total_sales_value": Decimal("12.34")}
    row = entry(summary=summary, products=[{"quantity": 999, "sales_value": "not-used"}])
    assert prepare([row])["branches"] == [expected_branch(summary=summary)]


def test_extra_annotations_do_not_authorize_rank_or_change_report_values():
    row = entry(authorized=False, eligible=False, growth="999", score=999, best_branch=True)
    row["permission"] = "owner"
    row["report"]["summary"]["rank"] = 1
    assert prepare([row]) == prepare([entry()])


def test_malformed_later_branch_leaves_inputs_unchanged_and_returns_no_partial_result():
    rows = [entry(2), entry(3, summary={"transaction_count": 1})]
    original = deepcopy(rows)
    with pytest.raises(ValueError):
        prepare(rows)
    assert rows == original


@pytest.mark.parametrize("field", ["business_id", "report_definition_context"])
def test_business_and_definition_arguments_are_explicitly_required(field):
    context = {"business_id": 1, "report_definition_context": definition_context()}
    del context[field]
    with pytest.raises(TypeError):
        prepare_branch_sales_comparison([], **context)


def test_safe_errors_do_not_expose_supplied_private_values():
    with pytest.raises(ValueError) as error:
        prepare([entry(business_id="private-business", currency="private-currency")])
    assert "private" not in str(error.value)


def test_results_are_deterministic_fresh_and_leave_all_original_inputs_unchanged():
    rows = [entry("branch-test", products=[{"metadata": {"notes": ["test"]}}]),
            entry(9), entry(2)]
    original = deepcopy(rows)
    expected = prepare(rows)
    for order in permutations(rows):
        assert prepare(order) == expected
    result = prepare(rows)
    assert result is not expected
    assert result["branches"] is not expected["branches"]
    assert result["branches"][0] is not expected["branches"][0]
    assert result["branches"][0]["summary"] is not rows[2]["report"]["summary"]
    result["branches"][0]["summary"]["total_quantity"] = 999
    result["branches"].append({})
    assert rows == original
    assert prepare(rows) == expected


def test_module_initialization_and_preparation_need_no_files_or_services(monkeypatch, blocked_external_services):
    source_path = Path(branch_comparison_report.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted = Mock(side_effect=AssertionError("Filesystem access attempted"))
    namespace = {"__name__": "branch_comparison_import_probe"}
    rows = [entry()]
    original = deepcopy(rows)
    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (io, "open"), (os, "open"), (os, "write"),
            (Path, "open"), (Path, "write_text"), (Path, "write_bytes"),
        ):
            guard.setattr(owner, name, attempted)
        exec(module_code, namespace)
        result = namespace["prepare_branch_sales_comparison"](
            rows, business_id=1, report_definition_context=definition_context(),
        )
    assert result["branches"] == [expected_branch()]
    assert rows == original
    attempted.assert_not_called()
    blocked_external_services.assert_not_called()
