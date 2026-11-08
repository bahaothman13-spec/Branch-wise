"""BE-45 Phase 2: require daily date coverage before forecasting."""

from forecast_reliability import get_forecast_availability
from sales_history import prepare_daily_sales_history


def get_date_aware_forecast_availability(
    observations,
    *,
    business_id,
    branch_id,
    product_variant_id,
    start_date,
    end_date,
    forecast_days,
    window_days,
):
    """Forecast supplied, authorized daily sales only when the interval is covered.

    The observation and date contracts are those of
    ``prepare_daily_sales_history``. That helper distinguishes an explicit
    zero-quantity date from a missing date, and aggregates multiple daily
    observations using its established behavior.

    Incomplete coverage returns unavailable with the missing dates and does
    not call the forecaster. Complete coverage passes chronological daily
    totals to the existing reliability helper. Invalid observations or scope
    raise ``ValueError``; invalid/insufficient forecast inputs follow the
    reliability helper's unavailable result.
    """
    history = prepare_daily_sales_history(
        observations,
        business_id,
        branch_id,
        product_variant_id,
        start_date,
        end_date,
    )
    missing_dates = history["missing_dates"]
    if missing_dates:
        return {
            "available": False,
            "forecast": None,
            "missing_dates": missing_dates,
        }

    availability = get_forecast_availability(
        [daily["quantity"] for daily in history["daily_totals"]],
        forecast_days,
        window_days,
    )
    return {
        "available": availability["available"],
        "forecast": availability["forecast"],
        "missing_dates": [],
    }
