"""Offline end-to-end regression tests for the forecast/replenishment pipeline."""

import builtins
from copy import deepcopy
from types import ModuleType
from unittest.mock import Mock
import socket
import sqlite3
import sys

import pytest

from replenishment_service import prepare_replenishment_recommendation


START_DATE = "2026-11-01"
END_DATE = "2026-11-03"


def observation(day, quantity, **scope):
    """Create a synthetic, explicitly authorized test observation."""
    return {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "date": day,
        "quantity": quantity,
        **scope,
    }


def complete_history():
    return [
        observation("2026-11-03", 6),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
    ]


def run_pipeline(observations, **changes):
    arguments = {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "forecast_days": 4,
        "window_days": 2,
        # Trusted synthetic stock input for this test only.
        "available_qty": 5,
    }
    arguments.update(changes)
    return prepare_replenishment_recommendation(observations, **arguments)


@pytest.fixture(autouse=True)
def block_external_operations(monkeypatch):
    attempted = Mock(side_effect=AssertionError("External operation attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted)
    monkeypatch.setattr(sqlite3, "connect", attempted)

    original_import = builtins.__import__
    forbidden_imports = {"flask", "mysql", "pymysql", "sqlalchemy"}

    def guarded_import(name, *args, **kwargs):
        if forbidden_imports.intersection(name.split(".")):
            attempted(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    for name, function_name in (
        ("db", "get_db_connection"),
        ("ai_service", "run_ai"),
        ("gemini_provider", "gemini_provider"),
        ("inventory_service", "get_inventory"),
        ("purchase_order_service", "create_purchase_order"),
        ("purchase_receiving_service", "receive_purchase"),
    ):
        module = ModuleType(name)
        setattr(module, function_name, attempted)
        monkeypatch.setitem(sys.modules, name, module)

    yield attempted
    attempted.assert_not_called()


def test_complete_daily_history_produces_forecast_and_recommendation():
    result = run_pipeline(complete_history())

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 5.0,
            "forecast_total": 20.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "recommendation": 15,
        "missing_dates": [],
    }


def test_missing_date_prevents_forecast_and_recommendation():
    result = run_pipeline([
        observation("2026-11-01", 2),
        observation("2026-11-03", 6),
    ])

    assert result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": ["2026-11-02"],
    }


def test_explicitly_observed_zero_sales_days_count_toward_coverage():
    result = run_pipeline([
        observation("2026-11-01", 0),
        observation("2026-11-02", 2),
        observation("2026-11-03", 0),
    ])

    assert result["available"] is True
    assert result["forecast"]["daily_average"] == 1.0
    assert result["missing_dates"] == []


def test_valid_zero_forecast_is_distinct_from_unavailable_forecast():
    zero_forecast = run_pipeline([
        observation("2026-11-01", 0),
        observation("2026-11-02", 0),
        observation("2026-11-03", 0),
    ], available_qty=0)
    unavailable_forecast = run_pipeline([
        observation("2026-11-01", 0),
        observation("2026-11-03", 0),
    ], available_qty=0)

    assert zero_forecast == {
        "available": True,
        "forecast": {
            "daily_average": 0.0,
            "forecast_total": 0.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "recommendation": 0,
        "missing_dates": [],
    }
    assert unavailable_forecast == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": ["2026-11-02"],
    }


@pytest.mark.parametrize(
    "scope_field",
    ["business_id", "branch_id", "product_variant_id"],
)
def test_other_scope_observations_do_not_affect_pipeline(scope_field):
    complete_records = [
        observation("2026-11-03", 1000, **{scope_field: 99}),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ]

    complete_result = run_pipeline(complete_records)

    assert complete_result == {
        "available": True,
        "forecast": {
            "daily_average": 5.0,
            "forecast_total": 20.0,
            "forecast_days": 4,
            "window_days": 2,
        },
        "recommendation": 15,
        "missing_dates": [],
    }

    incomplete_records = [
        observation("2026-11-01", 2),
        observation("2026-11-03", 6),
        observation("2026-11-02", 1000, **{scope_field: 99}),
    ]
    incomplete_result = run_pipeline(incomplete_records)

    assert incomplete_result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": ["2026-11-02"],
    }


def test_insufficient_window_history_prevents_recommendation():
    result = run_pipeline(complete_history(), window_days=4)

    assert result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": [],
    }


def test_duplicate_same_day_observations_follow_be39_aggregation():
    records = [
        observation("2026-11-01", 1),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
        observation("2026-11-03", 6),
    ]

    result = run_pipeline(records, window_days=3)

    assert result["available"] is True
    assert result["forecast"]["daily_average"] == pytest.approx(13 / 3)
    assert result["forecast"]["forecast_total"] == pytest.approx(52 / 3)
    assert result["recommendation"] == 13
    assert result["missing_dates"] == []


def test_caller_observation_records_remain_unchanged():
    records = complete_history()
    records[0]["extra"] = {"annotations": ["synthetic"]}
    original = deepcopy(records)

    run_pipeline(records)

    assert records == original


def test_explicit_forecast_and_window_periods_are_respected():
    result = run_pipeline(
        complete_history(),
        forecast_days=2,
        window_days=1,
        available_qty=2,
    )

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 6.0,
            "forecast_total": 12.0,
            "forecast_days": 2,
            "window_days": 1,
        },
        "recommendation": 10,
        "missing_dates": [],
    }


def test_entire_pipeline_uses_no_external_operations(block_external_operations):
    available = run_pipeline(complete_history())
    unavailable = run_pipeline([
        observation("2026-11-01", 2),
    ])

    assert available["available"] is True
    assert unavailable["recommendation"] is None
    block_external_operations.assert_not_called()
