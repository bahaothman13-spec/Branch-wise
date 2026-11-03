"""BE-39 Phase 1: aggregate supplied, already eligible daily sales records.

This is a provisional internal helper, not a database query or Flask API.
The caller must handle authorization, cancellation/return eligibility, variant
mapping, timestamp-to-day conversion, and completeness of source records.
A date with no observation is *missing*, never an assumed zero-sales day.
"""

from datetime import date, timedelta
import re


# Technical safeguards for this provisional helper, not business rules.
MAX_RECORDS = 10_000
MAX_DAYS = 366
MAX_SAFE_QUANTITY = 2**53 - 1  # Largest integer exactly represented by a float.


def _read_date(value, name):
    """Accept only normalized ISO dates such as '2026-11-03'."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a valid calendar date.") from None


def _read_id(value, name):
    """IDs are provisional positive integers or nonblank strings."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} must be a valid identifier.")
    return value


def prepare_daily_sales_history(
    observations, business_id, branch_id, product_variant_id, start_date, end_date
):
    """Return dated sales totals and gaps for one explicit scope and date interval.

    ``observations`` must be a list/tuple of dictionaries with keys:
    business_id, branch_id, product_variant_id, date, quantity.
    ``date`` is a normalized YYYY-MM-DD string; ``quantity`` is a
    nonnegative integer (explicit zero is allowed). Input may contain records
    from other scopes or dates; these are excluded after validation.

    Returned keys: business_id, branch_id, product_variant_id, start_date,
    end_date, daily_totals, missing_dates, has_full_date_coverage.
    Each daily_totals item has ``date`` and ``quantity``. Coverage describes
    supplied dates only; it does NOT prove completeness of the source data.
    Never pass sparse daily_totals quantities to BE-40 as if days were adjacent.
    """
    for name, value in (
        ("business_id", business_id),
        ("branch_id", branch_id),
        ("product_variant_id", product_variant_id),
    ):
        _read_id(value, name)

    first = _read_date(start_date, "start_date")
    last = _read_date(end_date, "end_date")
    number_of_days = (last - first).days + 1
    if not 1 <= number_of_days <= MAX_DAYS:
        raise ValueError("Date interval must be 1 to 366 days, inclusive.")

    if not isinstance(observations, (list, tuple)):
        raise ValueError("Observations must be a list or tuple.")
    if len(observations) > MAX_RECORDS:
        raise ValueError("Too many observations for one request.")

    totals = {}
    selected_ids = (business_id, branch_id, product_variant_id)

    for record in observations:
        if not isinstance(record, dict):
            raise ValueError("Each observation must be a dictionary.")

        record_ids = tuple(
            _read_id(record.get(name), name)
            for name in ("business_id", "branch_id", "product_variant_id")
        )
        record_date = _read_date(record.get("date"), "observation date")
        quantity = record.get("quantity")
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 0
            or quantity > MAX_SAFE_QUANTITY
        ):
            raise ValueError("Quantity must be a nonnegative supported integer.")

        # Keep integer ID 1 distinct from string ID '1' and from boolean True.
        matches_scope = all(
            type(actual) is type(expected) and actual == expected
            for actual, expected in zip(record_ids, selected_ids)
        )
        if not matches_scope or not first <= record_date <= last:
            continue

        day = record_date.isoformat()
        new_total = totals.get(day, 0) + quantity
        if new_total > MAX_SAFE_QUANTITY:
            raise ValueError("Daily quantity exceeds the supported integer range.")
        totals[day] = new_total

    daily_totals = []
    missing_dates = []
    for index in range(number_of_days):
        day = (first + timedelta(days=index)).isoformat()
        if day in totals:
            daily_totals.append({"date": day, "quantity": totals[day]})
        else:
            missing_dates.append(day)

    return {
        "business_id": business_id,
        "branch_id": branch_id,
        "product_variant_id": product_variant_id,
        "start_date": start_date,
        "end_date": end_date,
        "daily_totals": daily_totals,
        "missing_dates": missing_dates,
        "has_full_date_coverage": not missing_dates,
    }
