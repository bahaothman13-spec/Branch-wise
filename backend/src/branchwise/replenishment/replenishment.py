"""BE-41 Phase 1: provisional replenishment baseline, not final business policy.

The caller must eventually supply stock from branch_inventory.available_qty,
rather than total on-hand stock. BE-20 and database integration must establish
how authoritative availability is supplied.
This module does not fetch stock, change inventory, or create purchase orders.
"""

import math
from fractions import Fraction


def calculate_replenishment(forecast_demand, available_qty):
    """Return max(0, ceil(forecast_demand - available_qty)) as a Python integer.

    Provisional inputs: explicitly supplied nonnegative, finite int/float values
    within the finite float range. Booleans, strings, and missing values are
    rejected with ValueError. forecast_demand may be BE-40's forecast_total for
    the caller's chosen period; available_qty must be the available stock for
    the same item and branch, using the same quantity units.

    Integer values stay exact. Floats are interpreted using their decimal text
    representation, so 2.2 minus 1.2 is one unit before rounding upward. This
    avoids an extra recommendation caused by binary subtraction noise, without
    applying a tolerance or repairing an inaccurate forecast.

    This function and its integer output are not a final BE-41 API contract.
    Safety stock, supplier lead time, minimum orders, and reserved-stock policy
    require approved rules. No such adjustments or order actions occur here.
    """
    for name, quantity in (
        ("forecast_demand", forecast_demand),
        ("available_qty", available_qty),
    ):
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise ValueError(f"{name} must be a nonnegative finite number.")
        try:
            finite = math.isfinite(quantity)
        except OverflowError:
            finite = False
        if not finite or quantity < 0:
            raise ValueError(f"{name} must be a nonnegative finite number.")

    # Exact fractions of decimal text avoid float cancellation and ceiling errors.
    shortfall = Fraction(str(forecast_demand)) - Fraction(str(available_qty))
    return max(0, math.ceil(shortfall))
