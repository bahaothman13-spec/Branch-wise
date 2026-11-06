"""BE-48 checks with synthetic, in-memory inventory contributions only."""

import builtins
from copy import deepcopy
from datetime import date
from decimal import Decimal
import io
from itertools import permutations
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import Mock

import pytest

import inventory_snapshot_report
from inventory_snapshot_report import summarize_inventory_snapshot


class IntegerSubclass(int):
    pass


class StringSubclass(str):
    pass


def observation(contribution_id="test-contribution-1", **changes):
    """Return synthetic test data, not real stock or availability figures."""
    row = {
        "business_id": 1,
        "branch_id": 2,
        "snapshot_id": "test-snapshot-1",
        "snapshot_date": "2026-11-01",
        "contribution_id": contribution_id,
        "product_id": 3,
        "product_variant_id": 4,
        "quantity_unit": "each",
        "quantity_basis": "caller-defined-test-basis",
        "quantity": 2,
    }
    row.update(changes)
    return row


def summarize(rows, **changes):
    context = {
        "business_id": 1,
        "branch_id": 2,
        "snapshot_id": "test-snapshot-1",
        "snapshot_date": "2026-11-01",
    }
    context.update(changes)
    return summarize_inventory_snapshot(rows, **context)


def group(quantity, **changes):
    expected = {
        "product_id": 3,
        "product_variant_id": 4,
        "quantity_unit": "each",
        "quantity_basis": "caller-defined-test-basis",
        "quantity": quantity,
    }
    expected.update(changes)
    return expected


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Catch attempted service access even if the helper swallows an error."""
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
        "sales_performance_report", "report_export",
    }

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in forbidden:
            return attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield attempted
    attempted.assert_not_called()


def test_normal_aggregation_returns_only_explicit_snapshot_and_quantity_groups():
    result = summarize([
        observation(quantity=7),
        observation("test-contribution-2", quantity=3),
    ])
    assert result == {
        "business_id": 1,
        "branch_id": 2,
        "snapshot_id": "test-snapshot-1",
        "snapshot_date": "2026-11-01",
        "groups": [group(10)],
    }


def test_products_variants_units_and_bases_form_separate_groups():
    rows = [
        observation("a", product_id=9, quantity=7),
        observation("b", product_variant_id=8, quantity=5),
        observation("c", quantity_unit="box", quantity=2),
        observation("d", quantity_basis="other-test-basis", quantity=11),
        observation("e", quantity=3),
    ]
    assert summarize(rows)["groups"] == [
        group(2, quantity_unit="box"),
        group(3),
        group(11, quantity_basis="other-test-basis"),
        group(5, product_variant_id=8),
        group(7, product_id=9),
    ]


def test_typed_product_and_variant_identifiers_are_distinct_and_sorted():
    rows = [
        observation("a", product_id="1", product_variant_id=1),
        observation("b", product_id=1, product_variant_id="1"),
        observation("c", product_id=1, product_variant_id=1),
    ]
    assert summarize(rows)["groups"] == [
        group(2, product_id=1, product_variant_id=1),
        group(2, product_id=1, product_variant_id="1"),
        group(2, product_id="1", product_variant_id=1),
    ]


def test_label_text_is_preserved_without_case_folding_or_unit_conversion():
    rows = [
        observation("a", quantity_unit="each"),
        observation("b", quantity_unit="Each"),
        observation("c", quantity_unit=" each "),
    ]
    assert summarize(rows)["groups"] == [
        group(2, quantity_unit=" each "),
        group(2, quantity_unit="Each"),
        group(2, quantity_unit="each"),
    ]


@pytest.mark.parametrize("field", ["business_id", "branch_id", "snapshot_id"])
def test_out_of_scope_observation_rejects_the_entire_snapshot(field):
    rows = [observation(), observation("other", **{field: "other-scope"})]
    with pytest.raises(ValueError):
        summarize(rows)


@pytest.mark.parametrize("field,selected", [("business_id", 1), ("branch_id", 2), ("snapshot_id", 1)])
def test_scope_matching_does_not_coerce_integer_and_string_identifiers(field, selected):
    with pytest.raises(ValueError):
        summarize([observation(**{field: str(selected)})], **{field: selected})


def test_string_business_branch_and_integer_snapshot_identifiers_are_supported():
    row = observation(business_id="business-test", branch_id="branch-test", snapshot_id=5)
    result = summarize([row], business_id="business-test", branch_id="branch-test", snapshot_id=5)
    assert result["groups"] == [group(2)]
    assert result["snapshot_id"] == 5


def test_same_snapshot_identifier_cannot_pool_different_dates():
    rows = [observation(), observation("other", snapshot_date="2026-11-02")]
    with pytest.raises(ValueError):
        summarize(rows)


def test_valid_leap_day_snapshot_is_supported():
    result = summarize([observation(snapshot_date="2024-02-29")], snapshot_date="2024-02-29")
    assert result["groups"] == [group(2)]


@pytest.mark.parametrize("changes", [
    {}, {"quantity": 999}, {"product_id": 99}, {"product_variant_id": 99},
    {"quantity_unit": "box"}, {"quantity_basis": "other-test-basis"},
])
def test_repeated_contribution_identifiers_are_rejected_even_across_groups(changes):
    with pytest.raises(ValueError):
        summarize([observation(), observation(**changes)])


def test_repeating_the_same_observation_object_is_also_a_duplicate():
    row = observation()
    with pytest.raises(ValueError):
        summarize([row, row])


def test_integer_and_string_contribution_identifiers_are_distinct():
    result = summarize([observation(1, quantity=2), observation("1", quantity=3)])
    assert result["groups"] == [group(5)]


@pytest.mark.parametrize("rows", [[], ()])
def test_empty_input_does_not_invent_inventory_groups_or_quantities(rows):
    assert summarize(rows) == {
        "business_id": 1,
        "branch_id": 2,
        "snapshot_id": "test-snapshot-1",
        "snapshot_date": "2026-11-01",
        "groups": [],
    }


def test_explicit_zero_is_preserved_as_an_observed_group():
    assert summarize([observation(quantity=0)])["groups"] == [group(0)]


def test_signed_quantities_are_summed_without_classifying_their_meaning():
    result = summarize([
        observation("a", quantity=-7),
        observation("b", quantity=3),
        observation("c", product_variant_id=8, quantity=0),
    ])
    assert result["groups"] == [group(-4), group(0, product_variant_id=8)]


@pytest.mark.parametrize("first,second,expected", [
    (2**1000, 3, 2**1000 + 3),
    (-2**1000, 2**1000, 0),
], ids=["large-exact-sum", "large-cancellation-to-observed-zero"])
def test_large_integer_arithmetic_is_exact(first, second, expected):
    result = summarize([observation("a", quantity=first), observation("b", quantity=second)])
    assert result["groups"] == [group(expected)]
    assert type(result["groups"][0]["quantity"]) is int


@pytest.mark.parametrize("field", ["business_id", "branch_id", "snapshot_id"])
def test_requested_scope_identifiers_are_validated_even_for_empty_input(field):
    with pytest.raises(ValueError):
        summarize([], **{field: None})


@pytest.mark.parametrize("field", [
    "business_id", "branch_id", "snapshot_id", "contribution_id", "product_id", "product_variant_id",
])
def test_each_observation_identifier_rejects_booleans(field):
    with pytest.raises(ValueError):
        summarize([observation(**{field: True})])


@pytest.mark.parametrize("value", [
    None, False, 0, -1, 1.0, "", " \t ", [], {}, b"1", Decimal("1"),
    IntegerSubclass(1), StringSubclass("contribution"),
])
def test_invalid_identifier_types_and_values_are_not_coerced(value):
    with pytest.raises(ValueError):
        summarize([observation(value)])


@pytest.mark.parametrize("field", list(observation()))
def test_every_documented_observation_field_is_required(field):
    row = observation()
    del row[field]
    with pytest.raises(ValueError):
        summarize([row])


@pytest.mark.parametrize("value", [
    None, True, False, 1.0, float("nan"), float("inf"), "3", Decimal("3"),
    [], {}, set(), IntegerSubclass(3),
])
def test_quantity_must_be_a_builtin_integer(value):
    with pytest.raises(ValueError):
        summarize([observation(quantity=value)])


@pytest.mark.parametrize("field", ["quantity_unit", "quantity_basis"])
@pytest.mark.parametrize("value", [None, True, 1, "", " \t ", [], StringSubclass("each")])
def test_grouping_labels_must_be_nonblank_builtin_strings(field, value):
    with pytest.raises(ValueError):
        summarize([observation(**{field: value})])


@pytest.mark.parametrize("value", [
    None, True, "2026-11-1", "20261101", "2026-02-30", "2025-02-29",
    "2026-11-01T00:00:00", " 2026-11-01", "٢٠٢٦-١١-٠١", date(2026, 11, 1),
])
def test_requested_and_observed_snapshot_dates_are_validated(value):
    with pytest.raises(ValueError):
        summarize([], snapshot_date=value)
    with pytest.raises(ValueError):
        summarize([observation(snapshot_date=value)])


@pytest.mark.parametrize("rows", [None, {}, "rows", 3, True, iter([]), [None], ["row"]])
def test_invalid_containers_and_observations_are_rejected(rows):
    with pytest.raises(ValueError):
        summarize(rows)


@pytest.mark.parametrize("field", ["business_id", "branch_id", "snapshot_id", "snapshot_date"])
def test_scope_and_date_arguments_are_explicitly_required(field):
    context = {
        "business_id": 1, "branch_id": 2,
        "snapshot_id": "test-snapshot-1", "snapshot_date": "2026-11-01",
    }
    del context[field]
    with pytest.raises(TypeError):
        summarize_inventory_snapshot([], **context)


def test_validation_errors_do_not_echo_private_observation_values():
    with pytest.raises(ValueError) as error:
        summarize([observation(business_id="private-business", quantity="private-quantity")])
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status", [None, "low", "out", "reserved", "cancelled", "returned"])
def test_status_and_authorization_annotations_do_not_decide_inclusion_or_actions(status):
    row = observation(stock_status=status, authorized=False, eligible=False,
                      inventory_value="999", purchase_quantity=999)
    assert summarize([row]) == summarize([observation()])


def test_results_are_deterministic_fresh_and_leave_original_inputs_unchanged():
    rows = [
        observation("a", product_id="product-test", metadata={"notes": ["test"]}),
        observation("b", product_variant_id=8),
        observation("c", quantity=3),
        observation("d", quantity=-1),
    ]
    original = deepcopy(rows)
    expected = summarize(rows)
    for order in permutations(rows):
        assert summarize(order) == expected
    result = summarize(rows)
    assert result is not expected
    assert result["groups"] is not expected["groups"]
    assert result["groups"][0] is not expected["groups"][0]
    result["groups"][0]["quantity"] = 999
    result["groups"].append({})
    assert rows == original
    assert summarize(rows) == expected


def test_module_initialization_and_summary_need_no_files_or_services(monkeypatch, blocked_external_services):
    source_path = Path(inventory_snapshot_report.__file__)
    module_code = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    attempted = Mock(side_effect=AssertionError("Filesystem access attempted"))
    namespace = {"__name__": "inventory_snapshot_import_probe"}
    rows = [observation()]
    original = deepcopy(rows)
    with monkeypatch.context() as guard:
        for owner, name in (
            (builtins, "open"), (io, "open"), (os, "open"), (os, "write"),
            (Path, "open"), (Path, "write_text"), (Path, "write_bytes"),
        ):
            guard.setattr(owner, name, attempted)
        exec(module_code, namespace)
        result = namespace["summarize_inventory_snapshot"](
            rows, business_id=1, branch_id=2,
            snapshot_id="test-snapshot-1", snapshot_date="2026-11-01",
        )
    assert result["groups"] == [group(2)]
    assert rows == original
    attempted.assert_not_called()
    blocked_external_services.assert_not_called()
