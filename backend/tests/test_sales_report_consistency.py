"""BE-53 regression checks across the BE-47 sales report aggregations."""

from copy import deepcopy
from decimal import Decimal, localcontext

from sales_performance_report import calculate_sales_performance_report


def observation(
    *,
    transaction_id,
    product_id,
    date,
    quantity,
    sales_value,
):
    return {
        "business_id": 1,
        "branch_id": 2,
        "transaction_id": transaction_id,
        "product_id": product_id,
        "date": date,
        "quantity": quantity,
        "sales_value": sales_value,
        "currency": "JOD",
        "quantity_unit": "each",
        "sales_value_basis": "caller-defined-test-basis",
    }


def exact_sum(values):
    with localcontext() as context:
        context.prec = 100
        return sum(values, Decimal("0"))


def test_report_aggregations_reconcile_without_double_counting_transactions():
    rows = [
        observation(
            transaction_id="sale-1",
            product_id=1,
            date="2026-10-31",
            quantity=100,
            sales_value="100",
        ),
        observation(
            transaction_id="sale-1",
            product_id=1,
            date="2026-11-01",
            quantity=2,
            sales_value="0.1000000000000000000001",
        ),
        observation(
            transaction_id="sale-1",
            product_id=1,
            date="2026-11-01",
            quantity=1,
            sales_value="0.20",
        ),
        observation(
            transaction_id="sale-1",
            product_id=2,
            date="2026-11-01",
            quantity=3,
            sales_value="0.30",
        ),
        observation(
            transaction_id="sale-2",
            product_id=2,
            date="2026-11-01",
            quantity=4,
            sales_value="0.40",
        ),
        observation(
            transaction_id="sale-3",
            product_id=1,
            date="2026-11-03",
            quantity=1,
            sales_value="-0.0000000000000000000001",
        ),
        observation(
            transaction_id="sale-4",
            product_id=1,
            date="2026-11-04",
            quantity=200,
            sales_value="200",
        ),
    ]
    original = deepcopy(rows)

    report = calculate_sales_performance_report(
        rows,
        business_id=1,
        branch_id=2,
        start_date="2026-11-01",
        end_date="2026-11-03",
        currency="JOD",
        quantity_unit="each",
        sales_value_basis="caller-defined-test-basis",
    )

    summary = report["summary"]
    daily_totals = report["daily_totals"]
    products = report["products"]
    daily_products = report["daily_product_totals"]

    assert [day["date"] for day in daily_totals] == [
        "2026-11-01",
        "2026-11-03",
    ]
    assert summary == {
        "transaction_count": 3,
        "total_quantity": 11,
        "total_sales_value": Decimal("1.0000000000000000000000"),
    }
    assert exact_sum(day["total_sales_value"] for day in daily_totals) == (
        summary["total_sales_value"]
    )
    assert exact_sum(product["sales_value"] for product in products) == (
        summary["total_sales_value"]
    )

    for day in daily_totals:
        day_products = [
            entry for entry in daily_products if entry["date"] == day["date"]
        ]
        assert exact_sum(
            entry["total_sales_value"] for entry in day_products
        ) == day["total_sales_value"]
        assert sum(entry["total_quantity"] for entry in day_products) == (
            day["total_quantity"]
        )

    assert sum(product["quantity"] for product in products) == summary["total_quantity"]
    assert sum(day["total_quantity"] for day in daily_totals) == summary["total_quantity"]
    assert sum(entry["total_quantity"] for entry in daily_products) == (
        summary["total_quantity"]
    )

    # Sale 1 contributes to both product groups, so summing group counts would
    # overcount transactions; the report-level count remains distinct.
    assert sum(entry["transaction_count"] for entry in daily_products) == 4
    assert summary["transaction_count"] == 3
    assert report["products"] == [
        {
            "product_id": 1,
            "quantity": 4,
            "sales_value": Decimal("0.3000000000000000000000"),
        },
        {
            "product_id": 2,
            "quantity": 7,
            "sales_value": Decimal("0.70"),
        },
    ]
    assert rows == original
