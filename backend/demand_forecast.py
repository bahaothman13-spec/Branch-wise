"""BE-40 Phase 1: independent demand calculation, not a final API contract.

BE-39 must supply daily history in chronological order and decide how to handle
missing days, returns, and aggregation. This module cannot verify calendar dates
from quantities alone. It does not fill gaps or adjust historical sales.
"""

import math
from statistics import mean


def calculate_demand_forecast(historical_quantities, forecast_days, window_days):
    """Return a simple moving-average baseline using caller-supplied daily sales.

    Provisional inputs:
      historical_quantities: nonempty list/tuple, oldest day first. Each value
        must be a nonnegative, finite int/float representable as a Python float.
        Booleans, strings, and missing values are rejected, even outside the window.
      forecast_days: positive integer number of future days to estimate.
      window_days: positive integer number of most recent daily observations
        to average. The caller chooses both periods; neither has a default.

    Provisional output: a dictionary with daily_average (float), forecast_total
    (float), forecast_days (int), and window_days (int). The same daily average is
    used for every forecast day, so forecast_total = daily_average * forecast_days.
    Fractional estimates are kept without rounding; no trend, seasonality, or
    accuracy/confidence claim is made. Gemini is not involved in the calculation.

    Invalid input, empty/insufficient history, or a calculation that cannot fit
    finite floats raises ValueError without returning a forecast. Input is unchanged.
    """
    for name, days in (("forecast_days", forecast_days), ("window_days", window_days)):
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError(f"{name} must be a positive integer.")

    if not isinstance(historical_quantities, (list, tuple)) or not historical_quantities:
        raise ValueError("Historical quantities must be a nonempty list or tuple.")
    if len(historical_quantities) < window_days:
        raise ValueError("There is insufficient history for the requested window.")

    quantities = []
    for value in historical_quantities:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Historical quantities must be nonnegative finite numbers.")
        try:
            quantity = float(value)
        except OverflowError:
            raise ValueError("Historical quantities must fit finite floats.") from None
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError("Historical quantities must be nonnegative finite numbers.")
        quantities.append(quantity)

    daily_average = mean(quantities[-window_days:])
    try:
        forecast_total = daily_average * forecast_days
    except OverflowError:
        raise ValueError("Forecast total must fit a finite float.") from None
    if not math.isfinite(forecast_total):
        raise ValueError("Forecast total must fit a finite float.")

    return {
        "daily_average": daily_average,
        "forecast_total": forecast_total,
        "forecast_days": forecast_days,
        "window_days": window_days,
    }
