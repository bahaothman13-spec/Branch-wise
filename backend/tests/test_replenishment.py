"""In-memory checks for the provisional BE-41 replenishment baseline."""

import builtins
import io
import socket
import sqlite3
import sys
from decimal import Decimal
from types import ModuleType
from unittest.mock import Mock

import pytest

from demand_forecast import calculate_demand_forecast
from replenishment import calculate_replenishment


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Detect database or network attempts, including swallowed failures."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)
    fake_db = ModuleType("db")
    fake_db.get_db_connection = attempted_access
    monkeypatch.setitem(sys.modules, "db", fake_db)

    yield attempted_access

    attempted_access.assert_not_called()


@pytest.mark.parametrize(
    "forecast_demand,available_qty,expected",
    [
        (20, 7, 13),
        (7, 20, 0),
        (7, 7, 0),
        (7.5, 7.5, 0),
        (0, 7, 0),
        (0, 0, 0),
        (7, 0, 7),
        (0.0, 0.0, 0),
        (-0.0, 0, 0),
        (12.1, 2, 11),
        (1.01, 0, 2),
        (0.1, 0, 1),
        (2.2, 1.2, 1),
        (1.1, 0.1, 1),
        (3.1, 1.2, 2),
        (2, 2.1, 0),
        (10.0, 0, 10),
    ],
)
def test_baseline_covers_shortfalls_stock_coverage_and_rounding(
    forecast_demand, available_qty, expected
):
    result = calculate_replenishment(forecast_demand, available_qty)

    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize("available_qty", [2**53, float(2**53)])
def test_large_integer_shortfall_is_not_lost_by_float_conversion(available_qty):
    assert calculate_replenishment(2**53 + 1, available_qty) == 1


def test_large_finite_integer_difference_remains_exact():
    assert calculate_replenishment(10**300, 10**300 - 1) == 1


def test_large_finite_float_produces_an_integer_without_overflow():
    assert calculate_replenishment(1e308, 0) == 10**308


@pytest.mark.parametrize("field", ["forecast_demand", "available_qty"])
@pytest.mark.parametrize(
    "quantity",
    [
        None,
        "private-quantity",
        b"private-quantity",
        True,
        False,
        [],
        {},
        {1},
        object(),
        Decimal("1.25"),
        complex(1, 0),
        -1,
        -0.1,
        float("nan"),
        float("inf"),
        float("-inf"),
        10**400,
    ],
    ids=[
        "none", "text", "bytes", "true", "false", "list", "mapping", "set",
        "object", "decimal", "complex", "negative-integer", "negative-float",
        "nan", "positive-infinity", "negative-infinity", "overflow-integer",
    ],
)
def test_invalid_quantities_raise_safe_value_error(field, quantity):
    arguments = {"forecast_demand": 10, "available_qty": 2}
    arguments[field] = quantity

    with pytest.raises(ValueError) as error:
        calculate_replenishment(**arguments)

    assert "private-quantity" not in str(error.value)


@pytest.mark.parametrize(
    "forecast_demand,available_qty", [(0, -1), (float("nan"), 100), (1, 10**400)]
)
def test_invalid_inputs_are_checked_even_when_stock_might_cover_demand(
    forecast_demand, available_qty
):
    with pytest.raises(ValueError):
        calculate_replenishment(forecast_demand, available_qty)


def test_repeated_calls_produce_the_same_recommendation():
    results = [calculate_replenishment(13.5, 4.25) for _ in range(3)]

    assert results == [10, 10, 10]


def test_caller_can_supply_the_existing_forecast_total():
    forecast = calculate_demand_forecast([2, 4, 6, 8, 10], 7, 3)
    original_forecast = forecast.copy()

    result = calculate_replenishment(forecast["forecast_total"], 20)

    assert result == 36
    assert forecast == original_forecast


def test_calculation_has_no_database_network_or_file_side_effects(
    blocked_external_services, monkeypatch
):
    attempted_file_access = Mock(side_effect=AssertionError("File access attempted"))
    with monkeypatch.context() as file_guard:
        file_guard.setattr(builtins, "open", attempted_file_access)
        file_guard.setattr(io, "open", attempted_file_access)
        result = calculate_replenishment(12, 5)

    assert result == 7
    blocked_external_services.assert_not_called()
    attempted_file_access.assert_not_called()
