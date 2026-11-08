"""Tests for BE-45 date-aware forecast availability."""

from copy import deepcopy
from types import ModuleType
from unittest.mock import Mock
import socket
import sqlite3
import sys

import pytest

import forecast_availability
from forecast_availability import get_date_aware_forecast_availability


def observation(day, quantity, **changes):
    return {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "date": day,
        "quantity": quantity,
        **changes,
    }


def availability(records, start="2026-11-01", end="2026-11-03", **changes):
    arguments = {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "start_date": start,
        "end_date": end,
        "forecast_days": 4,
        "window_days": 2,
    }
    arguments.update(changes)
    return get_date_aware_forecast_availability(records, **arguments)


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch):
    attempted = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)
    monkeypatch.setattr(sqlite3, "connect", attempted)

    for name, function_name in (
        ("db", "get_db_connection"),
        ("ai_service", "run_ai"),
        ("gemini_provider", "gemini_provider"),
        ("inventory_service", "get_inventory"),
    ):
        module = ModuleType(name)
        setattr(module, function_name, attempted)
        monkeypatch.setitem(sys.modules, name, module)

    yield attempted
    attempted.assert_not_called()


def test_complete_history_is_forecast_in_chronological_date_order():
    records = [
        observation("2026-11-03", 8),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
    ]

    result = availability(records)

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 6.0,
            "forecast_total": 24.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "missing_dates": [],
    }


def test_explicit_zero_sales_day_is_covered_and_forecast():
    records = [
        observation("2026-11-01", 0),
        observation("2026-11-02", 2),
        observation("2026-11-03", 0),
    ]

    result = availability(records)

    assert result["available"] is True
    assert result["forecast"]["daily_average"] == 1.0
    assert result["missing_dates"] == []


@pytest.mark.parametrize(
    "records,missing_dates",
    [
        (
            [observation("2026-11-02", 2), observation("2026-11-03", 3)],
            ["2026-11-01"],
        ),
        (
            [observation("2026-11-01", 1), observation("2026-11-03", 3)],
            ["2026-11-02"],
        ),
        (
            [observation("2026-11-01", 1), observation("2026-11-02", 2)],
            ["2026-11-03"],
        ),
        ([], ["2026-11-01", "2026-11-02", "2026-11-03"]),
    ],
)
def test_missing_dates_make_forecast_unavailable_without_calling_forecaster(
    records, missing_dates, monkeypatch
):
    calculator = Mock(side_effect=AssertionError("Forecast must not run"))
    monkeypatch.setattr(forecast_availability, "get_forecast_availability", calculator)

    assert availability(records) == {
        "available": False,
        "forecast": None,
        "missing_dates": missing_dates,
    }
    calculator.assert_not_called()


def test_reporting_interval_boundaries_are_inclusive_and_outside_days_ignored():
    records = [
        observation("2026-10-31", 100),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 100),
    ]

    result = availability(records, start="2026-11-01", end="2026-11-02")

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 3.0,
            "forecast_total": 12.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "missing_dates": [],
    }


def test_single_day_interval_supports_an_explicit_observed_zero():
    result = availability(
        [observation("2026-11-02", 0)],
        start="2026-11-02",
        end="2026-11-02",
        window_days=1,
    )

    assert result["available"] is True
    assert result["forecast"]["forecast_total"] == 0.0
    assert result["missing_dates"] == []


def test_complete_but_insufficient_history_is_unavailable_not_missing():
    result = availability(
        [observation("2026-11-01", 2), observation("2026-11-02", 4)],
        start="2026-11-01",
        end="2026-11-02",
        window_days=3,
    )

    assert result == {
        "available": False,
        "forecast": None,
        "missing_dates": [],
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"start_date": "2026-11-1"},
        {"end_date": "2026-02-30"},
        {"business_id": True},
    ],
)
def test_invalid_interval_or_scope_inputs_raise_value_error(arguments):
    with pytest.raises(ValueError):
        availability([], **arguments)


@pytest.mark.parametrize(
    "record",
    [
        observation("2026-11-01", -1),
        observation("2026-11-01", True),
    ],
)
def test_invalid_observations_raise_value_error(record):
    with pytest.raises(ValueError):
        availability([record])


def test_observation_from_another_variant_does_not_count_toward_coverage():
    result = availability([
        observation("2026-11-01", 2, product_variant_id=99),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ])

    assert result == {
        "available": False,
        "forecast": None,
        "missing_dates": ["2026-11-01"],
    }


@pytest.mark.parametrize("scope_field", ["business_id", "branch_id"])
def test_observations_from_another_scope_do_not_count_or_affect_forecast(scope_field):
    foreign_observations = [
        observation("2026-11-01", 100, **{scope_field: 99}),
        observation("2026-11-02", 100, **{scope_field: 99}),
        observation("2026-11-03", 100, **{scope_field: 99}),
    ]

    incomplete = availability([
        *foreign_observations,
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ])
    assert incomplete == {
        "available": False,
        "forecast": None,
        "missing_dates": ["2026-11-01"],
    }

    complete = availability([
        *foreign_observations,
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ])
    assert complete == {
        "available": True,
        "forecast": {
            "daily_average": 5.0,
            "forecast_total": 20.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "missing_dates": [],
    }


@pytest.mark.parametrize(
    "period_field",
    ["forecast_days", "window_days"],
)
def test_invalid_forecast_periods_follow_existing_unavailable_behavior(period_field):
    result = availability(
        [
            observation("2026-11-01", 2),
            observation("2026-11-02", 4),
            observation("2026-11-03", 6),
        ],
        **{period_field: 0},
    )

    assert result == {
        "available": False,
        "forecast": None,
        "missing_dates": [],
    }


def test_duplicate_daily_observations_use_existing_aggregation_and_forecast():
    result = availability([
        observation("2026-11-01", 1),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ])

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 5.0,
            "forecast_total": 20.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "missing_dates": [],
    }


def test_inputs_are_unchanged():
    records = [
        observation("2026-11-03", 3, notes={"labels": ["supplied"]}),
        observation("2026-11-01", 1),
        observation("2026-11-02", 0),
    ]
    original = deepcopy(records)

    first = availability(records)
    second = availability(records)

    assert first == second
    assert records == original


def test_complete_and_incomplete_paths_use_no_external_services(
    block_external_services,
):
    complete = availability([
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ])
    incomplete = availability([observation("2026-11-01", 2)])

    assert complete["available"] is True
    assert incomplete["available"] is False
    block_external_services.assert_not_called()
