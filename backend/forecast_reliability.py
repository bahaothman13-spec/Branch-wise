"""BE-45 Phase 1: forecast availability without AI or application integration."""

from demand_forecast import calculate_demand_forecast


def get_forecast_availability(historical_quantities, forecast_days, window_days):
    """Wrap BE-40 using a provisional internal format, not a Flask API contract.

    Inputs are passed directly to calculate_demand_forecast, which owns their
    validation and calculation. On success, return
    {"available": True, "forecast": original_forecast_result}. The original
    dictionary is returned unchanged, including valid zero-valued forecasts.

    A documented ValueError for invalid/insufficient history, invalid periods,
    or numeric overflow returns {"available": False, "forecast": None}. No
    exception details, inputs, or replacement forecast values are returned.
    Other exceptions propagate so programming errors are not hidden.

    Availability means the calculation succeeded; it is not an accuracy claim.
    This wrapper does not prepare history, retry, call AI, or access a database.
    """
    try:
        forecast = calculate_demand_forecast(
            historical_quantities, forecast_days, window_days
        )
    except ValueError:
        return {"available": False, "forecast": None}

    return {"available": True, "forecast": forecast}
