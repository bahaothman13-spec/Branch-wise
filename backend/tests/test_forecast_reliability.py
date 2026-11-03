"""Local checks for the provisional BE-45 forecast-availability wrapper."""

import socket
import sqlite3
import sys
from copy import deepcopy
from types import ModuleType
from unittest.mock import Mock

import pytest

import forecast_reliability
from forecast_reliability import get_forecast_availability


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Detect database, network, or AI calls, including swallowed failures."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)
    for name, function in (
        ("db", "get_db_connection"),
        ("ai_service", "run_ai"),
        ("gemini_provider", "gemini_provider"),
    ):
        fake_module = ModuleType(name)
        setattr(fake_module, function, attempted_access)
        monkeypatch.setitem(sys.modules, name, fake_module)

    yield attempted_access

    attempted_access.assert_not_called()


def test_successful_nonzero_forecast_is_available():
    result = get_forecast_availability([2, 4, 6, 8, 10], 7, 3)

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 8.0,
            "forecast_total": 56.0,
            "forecast_days": 7,
            "window_days": 3,
        },
    }


def test_successful_zero_forecast_is_still_available():
    result = get_forecast_availability([0, 0.0, 0], 7, 3)

    assert result == {
        "available": True,
        "forecast": {
            "daily_average": 0.0,
            "forecast_total": 0.0,
            "forecast_days": 7,
            "window_days": 3,
        },
    }


@pytest.mark.parametrize(
    "history,window_days",
    [
        ([], 1),
        ((), 1),
        ([1], 2),
        (None, 1),
        ("private-history", 1),
        ({"private-key": 5}, 1),
        ([None], 1),
        (["private-quantity"], 1),
        ([True], 1),
        ([-1], 1),
        ([-0.5], 1),
        ([float("nan")], 1),
        ([float("inf")], 1),
        ([10**400], 1),
        ([-1, 2, 4], 2),
    ],
    ids=[
        "empty-list", "empty-tuple", "insufficient", "none", "text", "mapping",
        "missing-quantity", "text-quantity", "boolean", "negative-integer",
        "negative-float", "nan", "infinity", "oversized-integer", "invalid-older-day",
    ],
)
def test_invalid_or_insufficient_history_has_no_forecast(history, window_days):
    result = get_forecast_availability(history, 7, window_days)

    assert result == {"available": False, "forecast": None}


@pytest.mark.parametrize("field", ["forecast_days", "window_days"])
@pytest.mark.parametrize("period", [0, -1, 1.5, True, None, "private-period"])
def test_invalid_periods_are_unavailable(field, period):
    arguments = {"forecast_days": 7, "window_days": 2}
    arguments[field] = period

    result = get_forecast_availability([2, 4], **arguments)

    assert result == {"available": False, "forecast": None}


@pytest.mark.parametrize("history,forecast_days", [([1e308], 2), ([1], 10**400)])
def test_numeric_overflow_is_unavailable_without_a_replacement(history, forecast_days):
    result = get_forecast_availability(history, forecast_days, 1)

    assert result == {"available": False, "forecast": None}


@pytest.mark.parametrize("history", [[2, 4, 6], (2, 4, 6), [-1, 4, 6], []])
def test_results_are_deterministic_and_input_remains_unchanged(history):
    original = deepcopy(history)

    first = get_forecast_availability(history, 3, 2)
    second = get_forecast_availability(history, 3, 2)

    assert first == second
    assert history == original


def test_wrapper_preserves_original_result_and_calls_calculator_once(monkeypatch):
    history = [2, 4, 6]
    forecast = {
        "daily_average": 5.0,
        "forecast_total": 15.0,
        "forecast_days": 3,
        "window_days": 2,
    }
    original_forecast = deepcopy(forecast)
    calculator = Mock(return_value=forecast)
    monkeypatch.setattr(forecast_reliability, "calculate_demand_forecast", calculator)

    result = get_forecast_availability(history, 3, 2)

    assert result == {"available": True, "forecast": original_forecast}
    assert result["forecast"] is forecast
    assert forecast == original_forecast
    calculator.assert_called_once_with(history, 3, 2)
    assert calculator.call_args.args[0] is history


def test_value_error_details_and_sensitive_inputs_are_not_exposed(
    monkeypatch, caplog, capsys
):
    private_details = "private-calculation-diagnostic-with-secret"
    history = ["private-sales-input"]
    calculator = Mock(side_effect=ValueError(private_details))
    monkeypatch.setattr(forecast_reliability, "calculate_demand_forecast", calculator)
    caplog.set_level("DEBUG")

    result = get_forecast_availability(history, 7, 1)

    assert result == {"available": False, "forecast": None}
    captured = capsys.readouterr()
    for private_value in (private_details, history[0]):
        assert private_value not in repr(result)
        assert private_value not in caplog.text
        assert private_value not in captured.out + captured.err
    calculator.assert_called_once_with(history, 7, 1)


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, AttributeError, OverflowError])
def test_unrelated_programming_errors_propagate(monkeypatch, error_type):
    error = error_type("Unexpected calculation failure")
    calculator = Mock(side_effect=error)
    monkeypatch.setattr(forecast_reliability, "calculate_demand_forecast", calculator)

    with pytest.raises(error_type) as caught:
        get_forecast_availability([2, 4], 7, 2)

    assert caught.value is error
    calculator.assert_called_once_with([2, 4], 7, 2)


def test_available_and_unavailable_paths_use_no_external_services(blocked_external_services):
    available = get_forecast_availability([2, 4], 3, 2)
    unavailable = get_forecast_availability([], 3, 2)

    assert available["available"] is True
    assert unavailable == {"available": False, "forecast": None}
    blocked_external_services.assert_not_called()
