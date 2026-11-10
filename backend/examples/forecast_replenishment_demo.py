"""Run a synthetic BE-39/BE-40/BE-41 forecast-to-replenishment demo."""

from pathlib import Path
import sys


BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
if str(BACKEND_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIRECTORY))

from replenishment_service import prepare_replenishment_recommendation


BUSINESS_ID = 1
BRANCH_ID = 2
PRODUCT_VARIANT_ID = 3
START_DATE = "2026-11-01"
END_DATE = "2026-11-03"
FORECAST_DAYS = 4
WINDOW_DAYS = 2
EXAMPLE_AVAILABLE_QUANTITY = 5


def _observation(day, quantity):
    return {
        "business_id": BUSINESS_ID,
        "branch_id": BRANCH_ID,
        "product_variant_id": PRODUCT_VARIANT_ID,
        "date": day,
        "quantity": quantity,
    }


def main():
    complete_history = [
        _observation("2026-11-01", 2),
        _observation("2026-11-02", 4),
        _observation("2026-11-03", 6),
    ]
    shared_arguments = {
        "business_id": BUSINESS_ID,
        "branch_id": BRANCH_ID,
        "product_variant_id": PRODUCT_VARIANT_ID,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "forecast_days": FORECAST_DAYS,
        "window_days": WINDOW_DAYS,
        "available_qty": EXAMPLE_AVAILABLE_QUANTITY,
    }

    print("Synthetic demonstration only; no database or inventory is accessed.")
    print(
        "Scope: business "
        f"{BUSINESS_ID}, branch {BRANCH_ID}, variant {PRODUCT_VARIANT_ID}"
    )
    print(f"Historical sales observations: {complete_history}")

    complete_result = prepare_replenishment_recommendation(
        complete_history, **shared_arguments
    )
    print("\nComplete-history scenario:")
    print(f"Forecast available: {complete_result['available']}")
    print(f"Forecast: {complete_result['forecast']}")
    print(
        "Example available quantity (synthetic): "
        f"{EXAMPLE_AVAILABLE_QUANTITY}"
    )
    print(
        "Replenishment recommendation: "
        f"{complete_result['recommendation']}"
    )

    incomplete_history = [
        complete_history[0],
        complete_history[2],
    ]
    incomplete_result = prepare_replenishment_recommendation(
        incomplete_history, **shared_arguments
    )
    print("\nMissing-history scenario:")
    print(f"Forecast available: {incomplete_result['available']}")
    print(f"Missing dates: {incomplete_result['missing_dates']}")
    print(f"Forecast: {incomplete_result['forecast']}")
    print(
        "Replenishment recommendation: "
        f"{incomplete_result['recommendation']}"
    )


if __name__ == "__main__":
    main()
