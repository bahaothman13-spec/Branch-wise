"""BE-41 Phase 2: compose date-aware forecast availability and replenishment.

This provisional internal service does not establish stock authority,
authorization, item/branch identity, unit compatibility, or forecast-horizon
appropriateness. Its caller is responsible for those conditions.
"""

from forecast_availability import get_date_aware_forecast_availability
from replenishment import calculate_replenishment


def prepare_replenishment_recommendation(
    observations,
    *,
    business_id,
    branch_id,
    product_variant_id,
    start_date,
    end_date,
    forecast_days,
    window_days,
    available_qty,
):
    """Return a provisional recommendation only when a forecast is available.

    Sales observations must already be authorized, and ``available_qty`` must
    be supplied by a trusted caller for the same item and branch and in the
    same quantity units as the forecast. This function cannot verify stock
    authority or whether the chosen forecast period is appropriate.

    Missing date coverage or an unavailable BE-45 forecast returns no
    recommendation. Otherwise the existing BE-41 calculator receives the
    forecast's ``forecast_total`` and the supplied stock quantity unchanged.
    No inventory is read or changed, and no purchase order is created.
    """
    availability = get_date_aware_forecast_availability(
        observations,
        business_id=business_id,
        branch_id=branch_id,
        product_variant_id=product_variant_id,
        start_date=start_date,
        end_date=end_date,
        forecast_days=forecast_days,
        window_days=window_days,
    )
    if not availability["available"]:
        return {
            "available": False,
            "forecast": None,
            "recommendation": None,
            "missing_dates": availability["missing_dates"],
        }

    forecast = availability["forecast"]
    recommendation = calculate_replenishment(
        forecast["forecast_total"], available_qty
    )
    return {
        "available": True,
        "forecast": forecast,
        "recommendation": recommendation,
        "missing_dates": availability["missing_dates"],
    }
