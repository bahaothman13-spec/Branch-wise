"""BE-47 Phase 1 checks using synthetic, in-memory sales lines only."""

import builtins
from copy import deepcopy
from decimal import Decimal, Inexact, Rounded, localcontext
import io
from itertools import permutations
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import sales_performance_report
from sales_performance_report import calculate_sales_performance_report


def observation(**changes):
    """Synthetic test data, never business figures or real sales records."""
    row = {
        "business_id": 1, "branch_id": 2, "transaction_id": "test-sale-1",
        "product_id": 3, "date": "2026-11-01", "quantity": 2,
        "sales_value": "0.30", "currency": "JOD", "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
    }
    row.update(changes)
    return row


def calculate(rows, **changes):
    context = {
        "business_id": 1, "branch_id": 2,
        "start_date": "2026-11-01", "end_date": "2026-11-03",
        "currency": "JOD", "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
    }
    context.update(changes)
    return calculate_sales_performance_report(rows, **context)


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
        "db", "mysql", "pymysql", "sqlalchemy", "flask", "google",
        "gemini_provider", "ai_client", "ai_service",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in forbidden:
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted
    attempted.assert_not_called()


def test_single_sale_has_explicit_scope_context_and_exact_totals():
    assert calculate([observation()]) == {
        "business_id": 1, "branch_id": 2,
        "start_date": "2026-11-01", "end_date": "2026-11-03",
        "currency": "JOD", "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
        "summary": {"transaction_count": 1, "total_quantity": 2,
                    "total_sales_value": Decimal("0.30")},
        "products": [{"product_id": 3, "quantity": 2, "sales_value": Decimal("0.30")}],
        "daily_totals": [{
            "date": "2026-11-01",
            "transaction_count": 1,
            "total_quantity": 2,
            "total_sales_value": Decimal("0.30"),
        }],
    }


def test_multiple_products_and_lines_count_each_transaction_once():
    rows = [
        observation(product_id=9, quantity=2, sales_value="1.10"),
        observation(product_id=3, quantity=3, sales_value="2.20"),
        observation(transaction_id="test-sale-2", product_id=9, quantity=4, sales_value="3.30"),
    ]
    result = calculate(rows)
    assert result["summary"] == {
        "transaction_count": 2, "total_quantity": 9, "total_sales_value": Decimal("6.60"),
    }
    assert result["products"] == [
        {"product_id": 3, "quantity": 3, "sales_value": Decimal("2.20")},
        {"product_id": 9, "quantity": 6, "sales_value": Decimal("4.40")},
    ]


def test_repeated_lines_still_contribute_but_do_not_repeat_transaction_count():
    row = observation()
    result = calculate([row, row.copy(), row])
    assert result["summary"] == {
        "transaction_count": 1, "total_quantity": 6, "total_sales_value": Decimal("0.90"),
    }
    assert result["products"][0]["quantity"] == 6


def test_daily_totals_count_distinct_transactions_and_preserve_existing_summaries():
    rows = [
        observation(date="2026-11-03", transaction_id="sale-2", product_id=9,
                    quantity=2, sales_value="0.1000000000000000000000000001"),
        observation(date="2026-11-01", transaction_id="sale-1", product_id=3,
                    quantity=1, sales_value="0.01"),
        observation(date="2026-11-03", transaction_id="sale-2", product_id=3,
                    quantity=3, sales_value="0.20"),
        observation(date="2026-11-03", transaction_id="sale-3", product_id=9,
                    quantity=4, sales_value="0.3000000000000000000000000002"),
    ]

    result = calculate(rows)

    assert result["daily_totals"] == [
        {
            "date": "2026-11-01",
            "transaction_count": 1,
            "total_quantity": 1,
            "total_sales_value": Decimal("0.01"),
        },
        {
            "date": "2026-11-03",
            "transaction_count": 2,
            "total_quantity": 9,
            "total_sales_value": Decimal("0.6000000000000000000000000003"),
        },
    ]
    assert result["summary"] == {
        "transaction_count": 3,
        "total_quantity": 10,
        "total_sales_value": Decimal("0.6100000000000000000000000003"),
    }
    assert result["products"] == [
        {"product_id": 3, "quantity": 4, "sales_value": Decimal("0.21")},
        {
            "product_id": 9,
            "quantity": 6,
            "sales_value": Decimal("0.4000000000000000000000000003"),
        },
    ]


def test_transaction_and_product_identifiers_preserve_types_without_ranking():
    rows = [
        observation(transaction_id="1", product_id="1", sales_value="999"),
        observation(transaction_id=1, product_id=1, sales_value="0.01"),
    ]
    result = calculate(rows)
    assert result["summary"]["transaction_count"] == 2
    assert [row["product_id"] for row in result["products"]] == [1, "1"]


def test_plain_decimal_strings_and_decimal_inputs_add_exactly():
    result = calculate([observation(sales_value="0.1"), observation(sales_value=Decimal("0.2"))])
    assert result["summary"]["total_sales_value"] == Decimal("0.3")
    assert type(result["summary"]["total_sales_value"]) is Decimal
    assert type(result["products"][0]["sales_value"]) is Decimal


def test_exact_sums_survive_small_context_large_values_and_cancellation():
    rows = [
        observation(sales_value="123456789012345678901234567890.123456789"),
        observation(sales_value="-123456789012345678901234567890.123456789"),
        observation(sales_value=Decimal("1E-40")),
        observation(sales_value=Decimal("2E-40")),
    ]
    with localcontext() as context:
        context.prec = 2
        context.Emax, context.Emin = 2, -2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        for order in permutations(rows):
            result = calculate(order)
            assert result["summary"]["total_sales_value"] == Decimal("3E-40")
            assert result["products"][0]["sales_value"] == Decimal("3E-40")
        assert not any(context.flags.values())
        assert (context.prec, context.Emax, context.Emin) == (2, 2, -2)


def test_sum_reserves_carry_digits_without_rounding():
    rows = [observation(sales_value="9.999") for _ in range(11)]
    assert calculate(rows)["summary"]["total_sales_value"] == Decimal("109.989")


def test_positive_exponent_cancellation_has_deterministic_decimal_representation():
    rows = [observation(sales_value=Decimal(value)) for value in ("-9E999", "1E1000", "-1E999")]
    expected = Decimal("0E999").as_tuple()
    for order in permutations(rows):
        result = calculate(order)
        assert result["summary"]["total_sales_value"].as_tuple() == expected
        assert result["products"][0]["sales_value"].as_tuple() == expected


def test_signed_numbers_are_algebraic_inputs_without_inferred_relationships():
    result = calculate([
        observation(quantity=-2, sales_value="1.25"),
        observation(quantity=3, sales_value="-0.50"),
    ])
    assert result["summary"] == {
        "transaction_count": 1, "total_quantity": 1, "total_sales_value": Decimal("0.75"),
    }


def test_zero_values_are_counted_as_supplied_observations():
    result = calculate([observation(quantity=0, sales_value="-0.00")])
    assert result["summary"] == {
        "transaction_count": 1, "total_quantity": 0, "total_sales_value": Decimal("0"),
    }
    assert len(result["products"]) == 1
    assert result["daily_totals"] == [{
        "date": "2026-11-01",
        "transaction_count": 1,
        "total_quantity": 0,
        "total_sales_value": Decimal("0"),
    }]


def test_quantities_use_exact_integer_arithmetic():
    result = calculate([observation(quantity=2**100), observation(quantity=1)])
    assert result["summary"]["total_quantity"] == 2**100 + 1


def test_date_filter_is_inclusive_and_counts_only_selected_transactions():
    rows = [observation(date=day, transaction_id=day) for day in (
        "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"
    )]
    result = calculate(rows)
    assert result["summary"] == {
        "transaction_count": 3, "total_quantity": 6, "total_sales_value": Decimal("0.90"),
    }
    assert [row["date"] for row in result["daily_totals"]] == [
        "2026-11-01",
        "2026-11-02",
        "2026-11-03",
    ]


def test_single_day_leap_date_range():
    result = calculate([observation(date="2024-02-29")],
                       start_date="2024-02-29", end_date="2024-02-29")
    assert result["summary"]["transaction_count"] == 1


@pytest.mark.parametrize("field", ["business_id", "branch_id"])
@pytest.mark.parametrize("value", [99, "1", "2"])
@pytest.mark.parametrize("day", ["2026-11-01", "2026-10-31"])
def test_mixed_business_or_branch_scope_is_rejected_even_outside_dates(field, value, day):
    with pytest.raises(ValueError):
        calculate([observation(), observation(**{field: value, "date": day, "authorized": True})])


def test_string_scope_is_supported_without_coercion():
    row = observation(business_id="business-test", branch_id="branch-test")
    assert calculate([row], business_id="business-test", branch_id="branch-test")["summary"]["transaction_count"] == 1


@pytest.mark.parametrize("rows", [[], (), [observation(date="2026-10-31")]])
def test_empty_selection_has_zero_totals_and_no_products(rows):
    result = calculate(rows)
    assert result["summary"] == {
        "transaction_count": 0, "total_quantity": 0, "total_sales_value": Decimal("0"),
    }
    assert type(result["summary"]["total_sales_value"]) is Decimal
    assert result["products"] == []
    assert result["daily_totals"] == []


@pytest.mark.parametrize("field", ["business_id", "branch_id"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "", " \t ", [], {}])
def test_invalid_requested_scope_identifiers(field, value):
    with pytest.raises(ValueError):
        calculate([], **{field: value})


@pytest.mark.parametrize("field", ["business_id", "branch_id", "transaction_id", "product_id"])
@pytest.mark.parametrize("value", [None, True, 0, -1, 1.0, "", " ", [], {}])
def test_invalid_observation_identifiers(field, value):
    with pytest.raises(ValueError):
        calculate([observation(**{field: value})])


@pytest.mark.parametrize("field", list(observation()))
def test_every_provisional_observation_field_is_required(field):
    row = observation()
    del row[field]
    with pytest.raises(ValueError):
        calculate([row])


@pytest.mark.parametrize("field", ["start_date", "end_date", "date"])
@pytest.mark.parametrize("value", [None, True, "2026-11-1", "20261101", "2026-02-30", "2025-02-29", "2026-11-01T00:00:00", " 2026-11-01"])
def test_invalid_dates(field, value):
    with pytest.raises(ValueError):
        if field == "date":
            calculate([observation(date=value)])
        else:
            calculate([], **{field: value})


def test_reversed_date_range_is_invalid():
    with pytest.raises(ValueError):
        calculate([], start_date="2026-11-04")


@pytest.mark.parametrize("value", [None, True, False, 1.0, 1.5, "2", Decimal("2"), [], {}])
def test_invalid_quantity_is_not_coerced(value):
    with pytest.raises(ValueError):
        calculate([observation(quantity=value)])


@pytest.mark.parametrize("value", [
    None, True, 1, 0.1, float("nan"), float("inf"), "", "NaN", "Infinity",
    "1e2", "+1.2", " 1.2", "1,000", ".5", "1.", "١٢", [], {},
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
    Decimal("1E1001"), Decimal("1E-1001"), Decimal("1" * 1001), "1" * 1003,
])
def test_invalid_sales_value_is_rejected(value):
    with pytest.raises(ValueError):
        calculate([observation(sales_value=value)])


def test_supported_decimal_size_boundaries_are_exact():
    rows = [observation(sales_value=Decimal("1E1000")),
            observation(sales_value=Decimal("1E-1000")),
            observation(sales_value=Decimal("9" * 1000))]
    expected = Decimal("1" + "9" * 1000 + "." + "0" * 999 + "1")
    assert calculate(rows)["summary"]["total_sales_value"] == expected


@pytest.mark.parametrize("field", ["currency", "quantity_unit", "sales_value_basis"])
@pytest.mark.parametrize("value", [None, True, 1, "", " \t ", []])
def test_requested_and_observed_context_labels_must_be_explicit(field, value):
    with pytest.raises(ValueError):
        calculate([], **{field: value})
    with pytest.raises(ValueError):
        calculate([observation(**{field: value})])


@pytest.mark.parametrize("field,value", [
    ("currency", "USD"), ("currency", "jod"), ("quantity_unit", "box"),
    ("sales_value_basis", "other-test-basis"),
])
def test_mixed_reporting_context_is_rejected_even_outside_dates(field, value):
    with pytest.raises(ValueError):
        calculate([observation(date="2026-10-31", **{field: value})])


@pytest.mark.parametrize("rows", [None, "rows", {}, 1, True, iter([]), [None], ["row"]])
def test_invalid_observation_containers_or_rows(rows):
    with pytest.raises(ValueError):
        calculate(rows)


def test_invalid_unselected_lines_are_not_silently_skipped():
    with pytest.raises(ValueError):
        calculate([observation(date="2026-10-31", sales_value=None)])


def test_errors_do_not_disclose_supplied_values():
    with pytest.raises(ValueError) as error:
        calculate([observation(business_id="private-business", product_id="private-product")])
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status", [None, "completed", "cancelled", "returned", "unknown"])
def test_extra_status_or_eligibility_fields_do_not_decide_inclusion(status):
    assert calculate([observation(status=status, authorized=False, eligible=False)]) == calculate([observation()])


def test_results_are_deterministic_across_input_order_and_do_not_mutate_inputs():
    rows = [observation(product_id=9), observation(product_id="a", transaction_id=2),
            observation(product_id=3, sales_value="0.01", metadata={"notes": ["test"]})]
    original = deepcopy(rows)
    expected = calculate(rows)
    for order in permutations(rows):
        assert calculate(order) == expected
    assert calculate(rows) == expected
    assert rows == original
    result = calculate(rows)
    result["products"][0]["quantity"] = 999
    result["products"].append({})
    result["summary"]["transaction_count"] = 999
    assert rows == original
    assert calculate(rows) == expected


def test_module_initialization_and_calculation_need_no_files_or_services(monkeypatch, blocked_external_services):
    source_path = Path(sales_performance_report.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted = Mock(side_effect=AssertionError("File access attempted"))
    namespace = {"__name__": "sales_report_import_probe"}
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", attempted)
        guard.setattr(io, "open", attempted)
        guard.setattr(Path, "open", attempted)
        exec(module_code, namespace)
        result = namespace["calculate_sales_performance_report"](
            [observation()], business_id=1, branch_id=2,
            start_date="2026-11-01", end_date="2026-11-03",
            currency="JOD", quantity_unit="each", sales_value_basis="caller-defined-test-basis",
        )
    assert result["summary"]["total_sales_value"] == Decimal("0.30")
    attempted.assert_not_called()
    blocked_external_services.assert_not_called()
