"""Tests for the provisional, database-free BE-39 daily-history aggregator."""

from copy import deepcopy
import socket
import sqlite3

import pytest

from sales_history import MAX_SAFE_QUANTITY, prepare_daily_sales_history


def observation(day="2026-11-01", quantity=5, business_id=1, branch_id=2, product_variant_id=3):
    return {
        "date": day,
        "quantity": quantity,
        "business_id": business_id,
        "branch_id": branch_id,
        "product_variant_id": product_variant_id,
    }


def prepare(records, start="2026-11-01", end="2026-11-03", **changes):
    args = {"business_id": 1, "branch_id": 2, "product_variant_id": 3,
            "start_date": start, "end_date": end}
    args.update(changes)
    return prepare_daily_sales_history(records, **args)


def test_same_day_totals_sorted_and_missing_middle_day():
    result = prepare([observation("2026-11-03", 10), observation("2026-11-01", 5),
                      observation("2026-11-01", 3)])
    assert result["daily_totals"] == [
        {"date": "2026-11-01", "quantity": 8},
        {"date": "2026-11-03", "quantity": 10},
    ]
    assert result["missing_dates"] == ["2026-11-02"]
    assert result["has_full_date_coverage"] is False


def test_explicit_zero_is_observed_not_missing():
    result = prepare([observation("2026-11-01", 0),
                      observation("2026-11-02", 2), observation("2026-11-03", 0)])
    assert [row["quantity"] for row in result["daily_totals"]] == [0, 2, 0]
    assert result["missing_dates"] == []
    assert result["has_full_date_coverage"] is True


def test_empty_history_marks_every_date_missing():
    result = prepare([])
    assert result["daily_totals"] == []
    assert result["missing_dates"] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert result["has_full_date_coverage"] is False


def test_leading_internal_and_trailing_gaps():
    result = prepare([observation("2026-11-02", 2), observation("2026-11-04", 4)],
                     "2026-11-01", "2026-11-05")
    assert result["missing_dates"] == ["2026-11-01", "2026-11-03", "2026-11-05"]


def test_business_branch_variant_isolation():
    result = prepare([observation("2026-11-01", 5), observation("2026-11-01", 100, business_id=8),
                      observation("2026-11-01", 100, branch_id=8),
                      observation("2026-11-01", 100, product_variant_id=8)])
    assert result["daily_totals"] == [{"date": "2026-11-01", "quantity": 5}]
    assert result["business_id"] == 1
    assert result["branch_id"] == 2
    assert result["product_variant_id"] == 3


def test_identifier_types_do_not_cross_match():
    result = prepare([observation("2026-11-01", 9, business_id="1"),
                      observation("2026-11-01", 7)])
    assert result["daily_totals"] == [{"date": "2026-11-01", "quantity": 7}]


def test_records_outside_date_interval_are_not_counted():
    result = prepare([observation("2026-10-31", 8), observation("2026-11-01", 4),
                      observation("2026-11-04", 6)])
    assert result["daily_totals"] == [{"date": "2026-11-01", "quantity": 4}]
    assert result["missing_dates"] == ["2026-11-02", "2026-11-03"]


def test_single_date_interval_is_inclusive():
    result = prepare([observation("2026-11-01", 2)], "2026-11-01", "2026-11-01")
    assert result["daily_totals"] == [{"date": "2026-11-01", "quantity": 2}]
    assert result["has_full_date_coverage"] is True


def test_leap_day_is_a_real_date():
    result = prepare([observation("2024-02-29", 2)], "2024-02-28", "2024-03-01")
    assert result["missing_dates"] == ["2024-02-28", "2024-03-01"]


def test_input_is_not_modified_and_results_repeat():
    records = [observation("2026-11-03", 1), observation("2026-11-01", 4)]
    before = deepcopy(records)
    assert prepare(records) == prepare(records)
    assert records == before


def test_tuple_observations_supported():
    assert prepare((observation(),))["daily_totals"] == [
        {"date": "2026-11-01", "quantity": 5}]


@pytest.mark.parametrize("value", [None, 1, True, {}, "", "2026-11-1", "20261101",
                                    "2026-02-30", "2025-02-29", "hello"])
def test_invalid_interval_dates_rejected(value):
    with pytest.raises(ValueError):
        prepare([], start=value)


@pytest.mark.parametrize("value", [None, 1, False, [], "2026-11-1", "2026-02-30", "20261101"])
def test_invalid_observation_dates_rejected(value):
    with pytest.raises(ValueError):
        prepare([observation(day=value)])


def test_reversed_date_interval_rejected():
    with pytest.raises(ValueError):
        prepare([], "2026-11-03", "2026-11-01")


def test_oversized_date_interval_rejected():
    with pytest.raises(ValueError):
        prepare([], "2026-01-01", "2027-01-02")


def test_full_366_day_leap_year_allowed():
    result = prepare([], "2024-01-01", "2024-12-31")
    assert len(result["missing_dates"]) == 366


@pytest.mark.parametrize("value", [None, False, True, 0, -1, "", "  ", [], {}])
def test_invalid_requested_identifiers_rejected(value):
    with pytest.raises(ValueError):
        prepare([], business_id=value)


@pytest.mark.parametrize("name", ["business_id", "branch_id", "product_variant_id", "date", "quantity"])
def test_missing_observation_fields_rejected(name):
    row = observation()
    del row[name]
    with pytest.raises(ValueError):
        prepare([row])


@pytest.mark.parametrize("value", [None, False, True, 0, -4, "", "  ", [], {}])
def test_invalid_observation_identifiers_rejected(value):
    with pytest.raises(ValueError):
        prepare([observation(branch_id=value)])


@pytest.mark.parametrize("value", [None, True, False, -1, 2.0, 2.5, float('inf'),
                                    float('nan'), "5", [], {}])
def test_invalid_quantities_rejected(value):
    with pytest.raises(ValueError):
        prepare([observation(quantity=value)])


def test_largest_supported_quantity_allowed():
    result = prepare([observation(quantity=MAX_SAFE_QUANTITY)])
    assert result["daily_totals"][0]["quantity"] == MAX_SAFE_QUANTITY


def test_excessive_quantity_rejected():
    with pytest.raises(ValueError):
        prepare([observation(quantity=MAX_SAFE_QUANTITY + 1)])


def test_sum_overflow_rejected():
    with pytest.raises(ValueError):
        prepare([observation(quantity=MAX_SAFE_QUANTITY), observation(quantity=1)])


@pytest.mark.parametrize("value", [None, {}, "text", 3, True, (observation(), None)])
def test_invalid_observations_container_or_record_rejected(value):
    with pytest.raises(ValueError):
        prepare(value)


def test_record_count_guard():
    rows = [observation()] * 10001
    with pytest.raises(ValueError):
        prepare(rows)


def test_unselected_records_still_must_be_well_formed():
    # The caller owns record eligibility; bad supplied data is not silently ignored.
    with pytest.raises(ValueError):
        prepare([observation(business_id=99, quantity=-1)])


def test_extra_observation_fields_are_ignored():
    row = observation()
    row["irrelevant_note"] = "not part of the provisional contract"
    assert prepare([row])["daily_totals"][0]["quantity"] == 5


def test_no_network_or_database_needed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External access was attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    result = prepare([observation("2026-11-01", 3)])
    assert result["daily_totals"] == [{"date": "2026-11-01", "quantity": 3}]
