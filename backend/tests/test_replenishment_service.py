"""Tests for BE-41 forecast-to-replenishment composition."""

from copy import deepcopy
from types import ModuleType
from unittest.mock import Mock
import builtins
import socket
import sqlite3
import sys

import pytest

import replenishment_service
from replenishment_service import prepare_replenishment_recommendation


def observation(day, quantity):
    return {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "date": day,
        "quantity": quantity,
    }


def prepare(records, **changes):
    arguments = {
        "business_id": 1,
        "branch_id": 2,
        "product_variant_id": 3,
        "start_date": "2026-11-01",
        "end_date": "2026-11-03",
        "forecast_days": 4,
        "window_days": 2,
        "available_qty": 5,
    }
    arguments.update(changes)
    return prepare_replenishment_recommendation(records, **arguments)


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch):
    attempted = Mock(side_effect=AssertionError("External access attempted"))
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
    ):
        module = ModuleType(name)
        setattr(module, function_name, attempted)
        monkeypatch.setitem(sys.modules, name, module)

    yield attempted
    attempted.assert_not_called()


def complete_history():
    return [
        observation("2026-11-03", 6),
        observation("2026-11-01", 2),
        observation("2026-11-02", 4),
    ]


def test_complete_coverage_returns_forecast_and_replenishment_quantity():
    result = prepare(complete_history(), available_qty=5)

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


def test_missing_dates_return_no_recommendation_and_skip_calculation(monkeypatch):
    calculate = Mock(side_effect=AssertionError("Unavailable forecast was used"))
    monkeypatch.setattr(replenishment_service, "calculate_replenishment", calculate)

    result = prepare([
        observation("2026-11-01", 2),
        observation("2026-11-03", 6),
    ])

    assert result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": ["2026-11-02"],
    }
    calculate.assert_not_called()


def test_insufficient_forecast_history_returns_no_recommendation(monkeypatch):
    calculate = Mock(side_effect=AssertionError("Unavailable forecast was used"))
    monkeypatch.setattr(replenishment_service, "calculate_replenishment", calculate)

    result = prepare(
        [observation("2026-11-01", 2)],
        start_date="2026-11-01",
        end_date="2026-11-01",
        window_days=2,
    )

    assert result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": [],
    }
    calculate.assert_not_called()


def test_valid_zero_forecast_is_available_and_calculated():
    result = prepare(
        [
            observation("2026-11-01", 0),
            observation("2026-11-02", 0),
            observation("2026-11-03", 0),
        ],
        available_qty=0,
    )

    assert result == {
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


def test_existing_replenishment_formula_is_used_without_adjustment():
    result = prepare(complete_history(), available_qty=21)

    assert result["forecast"]["forecast_total"] == 20.0
    assert result["recommendation"] == 0


def test_forecast_total_and_available_stock_are_passed_unchanged(monkeypatch):
    forecast = {"forecast_total": 12.75, "custom": "preserved"}
    availability = {
        "available": True,
        "forecast": forecast,
        "missing_dates": [],
    }
    get_availability = Mock(return_value=availability)
    calculate = Mock(return_value=17)
    monkeypatch.setattr(
        replenishment_service,
        "get_date_aware_forecast_availability",
        get_availability,
    )
    monkeypatch.setattr(replenishment_service, "calculate_replenishment", calculate)
    stock = 1.25

    result = prepare(complete_history(), available_qty=stock)

    assert result == {
        "available": True,
        "forecast": forecast,
        "recommendation": 17,
        "missing_dates": [],
    }
    assert calculate.call_args.args == (12.75, stock)
    assert calculate.call_args.args[1] is stock


@pytest.mark.parametrize(
    "available_qty",
    [None, "5", True, -1, float("nan"), float("inf")],
)
def test_invalid_stock_preserves_existing_value_error_behavior(available_qty):
    with pytest.raises(ValueError):
        prepare(complete_history(), available_qty=available_qty)


@pytest.mark.parametrize("period_field", ["forecast_days", "window_days"])
def test_invalid_period_preserves_existing_unavailable_behavior(period_field):
    result = prepare(complete_history(), **{period_field: 0})

    assert result == {
        "available": False,
        "forecast": None,
        "recommendation": None,
        "missing_dates": [],
    }


def test_caller_inputs_are_not_mutated():
    records = complete_history()
    records[0]["extra"] = {"labels": ["caller"]}
    original = deepcopy(records)

    first = prepare(records)
    second = prepare(records)

    assert first == second
    assert records == original


def test_composition_uses_no_external_services(block_external_services):
    result = prepare(complete_history())

    assert result["available"] is True
    block_external_services.assert_not_called()
