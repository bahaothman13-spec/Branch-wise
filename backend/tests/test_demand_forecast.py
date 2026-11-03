"""Mocked checks for provisional BE-40 calculations, without external services."""

import socket
import sqlite3
import sys
from decimal import Decimal
from types import ModuleType
from unittest.mock import Mock

import pytest

from demand_forecast import calculate_demand_forecast


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Detect database or network attempts even if the calculation catches them."""
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


def test_recent_daily_sales_produce_the_expected_forecast():
    result = calculate_demand_forecast([2, 4, 6, 8, 10], 7, 3)

    assert result == {
        "daily_average": 8.0,
        "forecast_total": 56.0,
        "forecast_days": 7,
        "window_days": 3,
    }
    assert isinstance(result["daily_average"], float)
    assert isinstance(result["forecast_total"], float)


@pytest.mark.parametrize("forecast_days", [1, 3, 7, 30])
def test_different_forecast_periods_scale_the_same_daily_average(forecast_days):
    result = calculate_demand_forecast([3, 6], forecast_days, 2)

    assert result["daily_average"] == 4.5
    assert result["forecast_total"] == 4.5 * forecast_days
    assert result["forecast_days"] == forecast_days


@pytest.mark.parametrize("window_days,expected_average", [(1, 6.0), (2, 5.0), (3, 4.0)])
def test_window_uses_the_most_recent_values_in_chronological_order(
    window_days, expected_average
):
    result = calculate_demand_forecast([100, 200, 2, 4, 6], 2, window_days)

    assert result["daily_average"] == expected_average
    assert result["forecast_total"] == expected_average * 2
    assert result["window_days"] == window_days


def test_fractional_quantities_are_supported_without_rounding():
    fractional = calculate_demand_forecast([0.25, 0.5, 0.75], 3, 3)
    repeating = calculate_demand_forecast([1, 2, 2], 7, 3)

    assert fractional["daily_average"] == 0.5
    assert fractional["forecast_total"] == 1.5
    assert repeating["daily_average"] == pytest.approx(5 / 3)
    assert repeating["forecast_total"] == pytest.approx((5 / 3) * 7)
    assert repeating["daily_average"] != round(5 / 3, 2)


def test_observed_zero_sales_produce_a_zero_estimate():
    result = calculate_demand_forecast([0, 0.0, 0], 7, 3)

    assert result["daily_average"] == 0.0
    assert result["forecast_total"] == 0.0


def test_explicit_single_observation_window_is_supported():
    result = calculate_demand_forecast([7], 3, 1)

    assert result["daily_average"] == 7.0
    assert result["forecast_total"] == 21.0


@pytest.mark.parametrize("history", [[2, 4, 6], (2, 4, 6)])
def test_input_is_unchanged_and_results_are_repeatable(history):
    original = history.copy() if isinstance(history, list) else tuple(history)

    first = calculate_demand_forecast(history, 3, 2)
    second = calculate_demand_forecast(history, 3, 2)

    assert first == second
    assert first is not second
    assert history == original


@pytest.mark.parametrize("history,window_days", [([], 1), ((), 1), ([2], 2), ([2, 4], 3)])
def test_empty_or_insufficient_history_does_not_produce_a_forecast(history, window_days):
    with pytest.raises(ValueError):
        calculate_demand_forecast(history, 7, window_days)


@pytest.mark.parametrize(
    "history",
    [None, "private-history", {"private-key": 2}, {2, 4}, iter([2, 4]), 2, True],
    ids=["none", "text", "mapping", "set", "iterator", "number", "boolean"],
)
def test_invalid_history_containers_raise_safe_errors(history):
    with pytest.raises(ValueError) as error:
        calculate_demand_forecast(history, 7, 1)

    assert "private-history" not in str(error.value)
    assert "private-key" not in str(error.value)


@pytest.mark.parametrize(
    "quantity",
    [
        None, "private-quantity", b"private-quantity", True, False, [], {},
        object(), Decimal("1.25"), -1, -0.5, float("nan"), float("inf"),
        float("-inf"), 10**400,
    ],
    ids=[
        "none", "text", "bytes", "true", "false", "list", "mapping",
        "object", "decimal", "negative-integer", "negative-float", "nan",
        "positive-infinity", "negative-infinity", "unrepresentable-integer",
    ],
)
def test_invalid_quantities_are_rejected_without_echoing_values(quantity):
    with pytest.raises(ValueError) as error:
        calculate_demand_forecast([quantity], 7, 1)

    assert "private-quantity" not in str(error.value)


@pytest.mark.parametrize("quantity", [-1, None, float("nan"), float("inf")])
def test_invalid_older_values_are_rejected_even_outside_the_selected_window(quantity):
    with pytest.raises(ValueError):
        calculate_demand_forecast([quantity, 2, 4], 7, 2)


@pytest.mark.parametrize("field", ["forecast_days", "window_days"])
@pytest.mark.parametrize(
    "period",
    [0, -1, 1.5, "private-period", None, True, False, [], {}, float("inf"), float("nan")],
    ids=[
        "zero", "negative", "fractional", "text", "none", "true", "false",
        "list", "mapping", "infinity", "nan",
    ],
)
def test_invalid_periods_raise_safe_errors(field, period):
    arguments = {"forecast_days": 7, "window_days": 2}
    arguments[field] = period

    with pytest.raises(ValueError) as error:
        calculate_demand_forecast([2, 4], **arguments)

    assert "private-period" not in str(error.value)


def test_large_finite_quantities_do_not_overflow_while_averaging():
    result = calculate_demand_forecast([1e308, 1e308], 1, 2)

    assert result["daily_average"] == 1e308
    assert result["forecast_total"] == 1e308


@pytest.mark.parametrize("history,forecast_days", [([1e308], 2), ([1], 10**400)])
def test_unrepresentable_forecast_totals_raise_value_error(history, forecast_days):
    with pytest.raises(ValueError):
        calculate_demand_forecast(history, forecast_days, 1)


def test_calculation_uses_no_network_database_or_gemini(blocked_external_services, monkeypatch):
    fake_gemini = ModuleType("gemini_provider")
    fake_gemini.gemini_provider = blocked_external_services
    monkeypatch.setitem(sys.modules, "gemini_provider", fake_gemini)

    result = calculate_demand_forecast([2, 4, 6], 2, 3)

    assert result["forecast_total"] == 8.0
    blocked_external_services.assert_not_called()
