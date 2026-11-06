"""BE-47 Phase 1: calculations on supplied, authorized, eligible sales lines.

This provisional internal contract defines no database schema, Flask API, final
report columns, or ranking criteria. The caller owns authorization, branch
ownership, source completeness, line uniqueness, product mapping, date/timezone
conversion, and returns/cancellation/status eligibility. IDs and extra fields
such as ``authorized`` cannot establish authorization.
"""

from datetime import date
from decimal import Context, Decimal, localcontext
import re


# Technical input bounds, not currency precision or business rounding rules.
MAX_VALUE_DIGITS = 1000
MAX_VALUE_EXPONENT = 1000


def _read_identifier(value, name):
    if type(value) not in (int, str):
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} must be a valid identifier.")
    return value


def _read_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Dates must be valid calendar dates.") from None


def _read_label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Currency, quantity unit, and sales value basis must be explicit text.")
    return value


def _read_sales_value(value):
    if isinstance(value, str):
        if len(value) > MAX_VALUE_DIGITS + 2 or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
            raise ValueError("Sales value must be a supported plain decimal string.")
        value = Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Sales value must be a finite Decimal or plain decimal string.")
    parts = value.as_tuple()
    if len(parts.digits) > MAX_VALUE_DIGITS or abs(parts.exponent) > MAX_VALUE_EXPONENT:
        raise ValueError("Sales value exceeds the supported decimal size.")
    return value


def _sum_sales_values(values):
    """Allow enough digits for all decimal places, integer digits, and carries."""
    if not values:
        return Decimal("0")
    lowest_place = min(value.as_tuple().exponent for value in values)
    highest_place = max(value.adjusted() for value in values)
    carry_digits = len(str(len(values)))
    precision = max(1, highest_place - lowest_place + 1 + carry_digits)
    # A fresh context also avoids inheriting caller rounding, exponent limits,
    # flags, or traps. No quantization, conversion, or price multiplication.
    with localcontext(Context(prec=precision)):
        return sum(values[1:], values[0])


def calculate_sales_performance_report(
    observations, *, business_id, branch_id, start_date, end_date,
    currency, quantity_unit, sales_value_basis
):
    """Return exact summary and product aggregates for an inclusive date range.

    Provisional input contract:
    - observations is a list/tuple of dictionaries. Each requires business_id,
      branch_id, transaction_id, product_id, date, quantity, sales_value,
      currency, quantity_unit, and sales_value_basis.
    - IDs are positive built-in integers or nonblank built-in strings, without
      coercion. Every line must match the supplied business/branch and context,
      even outside the date range. Mismatches raise ValueError; these checks
      detect mixed input and do not authorize access.
    - Dates are normalized YYYY-MM-DD strings, already attributed by the caller.
      Both endpoints are required; start_date must not follow end_date.
    - quantity is a built-in integer. sales_value is a finite Decimal (constructed
      from exact input, never floats) or a plain decimal string, with an optional
      minus sign. Values support at most 1000 coefficient digits and a Decimal
      exponent from -1000 to 1000; strings have at most 1002 characters.
    - Signed numbers are summed algebraically as supplied. Signs do not classify
      returns or establish eligibility. No sign relationship is assumed.
    - Explicit, matching currency/unit/value-basis labels prevent mixing unlike
      values. Their meaning, including tax/discount treatment, is caller-owned.
      sales_value is the supplied line value, never a unit price to multiply.
    - transaction_id must identify a transaction within the selected scope;
      product_id is the caller's product grouping key. No mapping is inferred.
      Unknown fields, including statuses and authorization flags, are ignored.

    Provisional output contract:
    Return scope/date/context labels, summary (transaction_count, total_quantity,
    total_sales_value), and products (product_id, quantity, sales_value).
    Transaction counting deduplicates typed IDs; every selected line contributes
    to quantity/value totals, including repeated lines. Source-line deduplication
    is the caller's responsibility. Products sort by identifier: integers first,
    then strings, ascending within each type. This is not a performance ranking.
    Empty selections return zero totals and no products; they do not establish
    that the source is complete or that the business had no sales.

    Money stays Decimal with no rounding, currency conversion, or quantization.
    Inputs are unchanged, output containers are fresh, and invalid input raises
    ValueError without including supplied values or returning partial results.
    """
    _read_identifier(business_id, "business_id")
    _read_identifier(branch_id, "branch_id")
    first, last = _read_date(start_date), _read_date(end_date)
    if first > last:
        raise ValueError("start_date must not follow end_date.")
    context = {
        "currency": _read_label(currency),
        "quantity_unit": _read_label(quantity_unit),
        "sales_value_basis": _read_label(sales_value_basis),
    }
    if not isinstance(observations, (list, tuple)):
        raise ValueError("Observations must be a list or tuple.")

    transactions = set()
    products = {}
    all_values = []
    total_quantity = 0
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("Each observation must be a dictionary.")
        for name, selected in (("business_id", business_id), ("branch_id", branch_id)):
            actual = _read_identifier(row.get(name), name)
            if type(actual) is not type(selected) or actual != selected:
                raise ValueError("Each observation must match the selected business and branch.")
        transaction_id = _read_identifier(row.get("transaction_id"), "transaction_id")
        product_id = _read_identifier(row.get("product_id"), "product_id")
        row_date = _read_date(row.get("date"))
        quantity = row.get("quantity")
        if type(quantity) is not int:
            raise ValueError("Quantity must be an integer.")
        value = _read_sales_value(row.get("sales_value"))
        for name, selected in context.items():
            if _read_label(row.get(name)) != selected:
                raise ValueError("Each observation must match the selected reporting context.")
        if not first <= row_date <= last:
            continue

        transactions.add(transaction_id)
        total_quantity += quantity
        all_values.append(value)
        if product_id not in products:
            products[product_id] = {"quantity": 0, "values": []}
        products[product_id]["quantity"] += quantity
        products[product_id]["values"].append(value)

    product_totals = []
    for product_id in sorted(products, key=lambda identifier: (isinstance(identifier, str), identifier)):
        product = products[product_id]
        product_totals.append({
            "product_id": product_id,
            "quantity": product["quantity"],
            "sales_value": _sum_sales_values(product["values"]),
        })
    return {
        "business_id": business_id,
        "branch_id": branch_id,
        "start_date": start_date,
        "end_date": end_date,
        **context,
        "summary": {
            "transaction_count": len(transactions),
            "total_quantity": total_quantity,
            "total_sales_value": _sum_sales_values(all_values),
        },
        "products": product_totals,
    }
