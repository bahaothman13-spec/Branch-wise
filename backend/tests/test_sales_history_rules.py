"""Read-only sales history rules, authorization, and trusted snapshot tests."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from permissions import (
    ROLE_PERMISSIONS,
    SALES_VIEW,
    SALES_VIEW_AUTHORIZED,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from sale_cancellation_rules import SaleStatus
from sales_history_rules import (
    SaleHistoryLine,
    SaleHistoryRecord,
    get_sale_details,
    list_sales_history,
    paginate_read_only,
    validate_pagination,
)


BUSINESS_ID = 10
BRANCH_ID = 22


def principal(role=Role.BRANCH_MANAGER, *, business_id=BUSINESS_ID, branch_id=BRANCH_ID, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=(
            ROLE_PERMISSIONS[role]
            if permissions is None
            else frozenset(permissions)
        ),
    )


def line(
    *,
    product_id=50,
    product_name="Coffee",
    variant_id=101,
    variant_name="Large",
    quantity=2,
    unit_price=Decimal("1.235"),
    subtotal=Decimal("2.470"),
):
    return SaleHistoryLine(
        product_id=product_id,
        product_name=product_name,
        product_variant_id=variant_id,
        variant_name=variant_name,
        quantity=quantity,
        unit_price_snapshot=unit_price,
        line_subtotal=subtotal,
    )


def sale(
    sale_id=1,
    *,
    reference=None,
    business_id=BUSINESS_ID,
    branch_id=BRANCH_ID,
    date=None,
    responsible_user_id=701,
    responsible_user_name="Branch User",
    status=SaleStatus.COMPLETED,
    items=None,
    total=Decimal("2.470"),
):
    branch_name = f"Branch {branch_id}"
    return SaleHistoryRecord(
        sale_id=sale_id,
        sale_reference=reference or f"SALE-{sale_id:04d}",
        sale_date=date or datetime(2026, 11, 5, 12, tzinfo=timezone.utc),
        business_id=business_id,
        branch_id=branch_id,
        branch_scope=ResourceScope(business_id, branch_id),
        branch_name=branch_name,
        responsible_user_id=responsible_user_id,
        responsible_user_name=responsible_user_name,
        status=status,
        items=(line(),) if items is None else items,
        total_amount=total,
    )


def records():
    return [
        sale(1, date=datetime(2026, 11, 3, 12, tzinfo=timezone.utc)),
        sale(
            2,
            branch_id=23,
            responsible_user_id=702,
            date=datetime(2026, 11, 5, 12, tzinfo=timezone.utc),
            status=SaleStatus.CANCELLED,
        ),
        sale(
            3,
            business_id=11,
            branch_id=90,
            date=datetime(2026, 11, 6, 12, tzinfo=timezone.utc),
        ),
        sale(4, date=datetime(2026, 11, 5, 12, tzinfo=timezone.utc)),
    ]


def test_authorized_sales_listing_omits_foreign_business_and_other_branches():
    result = list_sales_history(principal(), records())

    assert [item.sale_id for item in result.data] == [4, 1]
    assert result.pagination.total_items == 2


def test_owner_can_view_all_authorized_business_branches():
    result = list_sales_history(principal(Role.OWNER), records())

    assert [item.sale_id for item in result.data] == [4, 2, 1]
    assert {item.branch_id for item in result.data} == {22, 23}


def test_owner_does_not_receive_another_business_sale():
    result = list_sales_history(principal(Role.OWNER), [sale(8, business_id=11, branch_id=88)])

    assert result.data == ()
    assert result.pagination.total_items == 0


def test_branch_manager_is_restricted_to_assigned_branch():
    result = list_sales_history(principal(), records())

    assert [item.branch_id for item in result.data] == [22, 22]


def test_employee_requires_authorized_sales_view_permission():
    employee = principal(Role.EMPLOYEE)
    result = list_sales_history(employee, records())

    assert [item.sale_id for item in result.data] == [4, 1]

    no_history_permission = principal(
        Role.EMPLOYEE,
        permissions={"inventory:view", "sales:record"},
    )
    with pytest.raises(PermissionError, match="Resource not found or access denied"):
        list_sales_history(no_history_permission, records())


def test_employee_cannot_use_sales_view_permission_as_a_substitute():
    employee = principal(Role.EMPLOYEE, permissions={SALES_VIEW})

    with pytest.raises(PermissionError, match="Resource not found or access denied"):
        list_sales_history(employee, records())


def test_reference_filter_matches_only_requested_sale():
    result = list_sales_history(principal(), records(), sale_reference="SALE-0001")

    assert [item.sale_id for item in result.data] == [1]


def test_date_filters_are_inclusive_and_compare_utc_calendar_dates():
    result = list_sales_history(
        principal(),
        records(),
        date_from="2026-11-05",
        date_to="2026-11-05",
    )

    assert [item.sale_id for item in result.data] == [4]


def test_responsible_user_filter():
    result = list_sales_history(principal(Role.OWNER), records(), responsible_user_id="702")

    assert [item.sale_id for item in result.data] == [2]


def test_branch_filter_is_applied_inside_the_authorized_scope():
    manager_result = list_sales_history(principal(), records(), branch_id=23)
    owner_result = list_sales_history(principal(Role.OWNER), records(), branch_id=23)

    assert manager_result.data == ()
    assert [item.sale_id for item in owner_result.data] == [2]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("COMPLETED", [4, 1]),
        ("CANCELLED", []),
        (SaleStatus.COMPLETED, [4, 1]),
    ],
)
def test_status_filter_uses_existing_sale_status_enum(status, expected):
    result = list_sales_history(principal(), records(), status=status)

    assert [item.sale_id for item in result.data] == expected


def test_owner_can_filter_and_view_cancelled_sales_without_deleting_them():
    result = list_sales_history(principal(Role.OWNER), records(), status="Cancelled")
    detail = get_sale_details(principal(Role.OWNER), records(), sale_id=2)

    assert [item.sale_id for item in result.data] == [2]
    assert detail.status is SaleStatus.CANCELLED
    assert detail.items[0].quantity == 2


@pytest.mark.parametrize(
    ("sort_order", "expected"),
    [("desc", [4, 1]), ("asc", [1, 4])],
)
def test_sorting_uses_timestamp_with_sale_id_tie_breaker(sort_order, expected):
    result = list_sales_history(principal(), records(), sort_order=sort_order)

    assert [item.sale_id for item in result.data] == expected


def test_pagination_matches_api_contract_metadata_and_slices_after_sorting():
    result = list_sales_history(principal(Role.OWNER), records(), page="2", per_page="2")

    assert [item.sale_id for item in result.data] == [1]
    assert result.pagination.page == 2
    assert result.pagination.per_page == 2
    assert result.pagination.total_items == 3
    assert result.pagination.total_pages == 2


def test_empty_pagination_reports_zero_total_pages():
    result = list_sales_history(principal(), [], page=1, per_page=20)

    assert result.data == ()
    assert result.pagination.total_items == 0
    assert result.pagination.total_pages == 0


def test_generic_pagination_is_read_only_and_has_default_contract_values():
    source = ["first", "second", "third"]
    result = paginate_read_only(source, page=1, per_page=2)

    assert result.data == ("first", "second")
    assert result.pagination.page == 1
    assert result.pagination.per_page == 2
    assert result.pagination.total_items == 3
    assert result.pagination.total_pages == 2
    assert source == ["first", "second", "third"]
    assert validate_pagination().page == 1
    assert validate_pagination().per_page == 20


def test_history_uses_the_unit_price_snapshot_stored_on_each_line():
    historical = sale(
        51,
        items=(line(unit_price=Decimal("1.235"), subtotal=Decimal("2.470")),),
        total=Decimal("2.470"),
    )
    detail = get_sale_details(principal(), [historical], sale_reference="SALE-0051")

    assert detail.items[0].unit_price_snapshot == Decimal("1.235")
    assert detail.items[0].line_subtotal == Decimal("2.470")
    assert detail.total_amount == Decimal("2.470")


def test_sale_details_include_full_authorized_transaction_snapshot():
    detail = get_sale_details(principal(), [sale(60)], sale_id="60")

    assert detail.sale_reference == "SALE-0060"
    assert detail.sale_date == datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    assert detail.branch_id == 22
    assert detail.branch_name == "Branch 22"
    assert detail.responsible_user_id == 701
    assert detail.responsible_user_name == "Branch User"
    assert detail.status is SaleStatus.COMPLETED
    assert detail.items == (line(),)
    assert detail.total_amount == Decimal("2.470")


def test_detail_lookup_by_reference_returns_only_authorized_details():
    detail = get_sale_details(principal(), records(), sale_reference="SALE-0001")

    assert detail.sale_id == 1
    assert detail.items[0].product_name == "Coffee"


@pytest.mark.parametrize("selector", [{"sale_id": 2}, {"sale_reference": "SALE-0002"}])
def test_direct_lookup_does_not_disclose_another_branch_sale(selector):
    with pytest.raises(PermissionError, match="Resource not found or access denied"):
        get_sale_details(principal(), records(), **selector)


@pytest.mark.parametrize("selector", [{"sale_id": 3}, {"sale_reference": "SALE-0003"}])
def test_direct_lookup_does_not_disclose_another_business_sale(selector):
    with pytest.raises(PermissionError, match="Resource not found or access denied"):
        get_sale_details(principal(Role.OWNER), records(), **selector)


def test_foreign_sale_lookup_uses_the_same_denial_as_a_missing_sale():
    with pytest.raises(PermissionError) as foreign:
        get_sale_details(principal(), records(), sale_id=2)
    with pytest.raises(PermissionError) as missing:
        get_sale_details(principal(), records(), sale_id=999)

    assert str(foreign.value) == str(missing.value)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sale_reference": ""}, "sale_reference"),
        ({"date_from": "2026-02-30"}, "date_from"),
        ({"date_to": datetime(2026, 1, 1, tzinfo=timezone.utc)}, "date_to"),
        ({"date_from": "2026-11-07", "date_to": "2026-11-06"}, "date_from"),
        ({"responsible_user_id": True}, "responsible_user_id"),
        ({"branch_id": 0}, "branch_id"),
        ({"status": "REFUNDED"}, "status"),
        ({"sort_order": "amount"}, "sort_order"),
        ({"page": 0}, "page"),
        ({"page": "1.5"}, "page"),
        ({"per_page": 101}, "per_page"),
    ],
)
def test_invalid_filters_and_pagination_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        list_sales_history(principal(), records(), **changes)


def test_detail_lookup_requires_exactly_one_valid_selector():
    with pytest.raises(ValueError, match="exactly one"):
        get_sale_details(principal(), records())
    with pytest.raises(ValueError, match="exactly one"):
        get_sale_details(principal(), records(), sale_id=1, sale_reference="SALE-0001")
    with pytest.raises(ValueError, match="sale_id"):
        get_sale_details(principal(), records(), sale_id=True)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"branch_scope": ResourceScope(999, 22)}, "branch context"),
        ({"business_id": 11}, "branch context"),
        ({"branch_id": 0}, "branch_id"),
        ({"sale_date": datetime(2026, 11, 5)}, "timezone-aware"),
        ({"responsible_user_id": False}, "responsible_user_id"),
        ({"status": "Refunded"}, "invalid status"),
        ({"total_amount": Decimal("2.469")}, "total does not match"),
        ({"total_amount": Decimal("1000000000.000")}, "DECIMAL\\(12,3\\)"),
    ],
)
def test_malformed_trusted_sale_records_are_rejected(changes, message):
    malformed = replace(sale(70), **changes)

    with pytest.raises(ValueError, match=message):
        list_sales_history(principal(), [malformed])


@pytest.mark.parametrize(
    "bad_line",
    [
        line(subtotal=Decimal("2.469")),
        line(unit_price=Decimal("1.2345")),
        line(quantity=True),
        line(product_name=" "),
        line(subtotal=Decimal("1000000000.000")),
    ],
)
def test_malformed_trusted_sale_lines_are_rejected(bad_line):
    malformed = sale(71, items=(bad_line,))

    with pytest.raises(ValueError):
        list_sales_history(principal(), [malformed])


def test_duplicate_trusted_sale_ids_are_rejected_instead_of_returning_ambiguous_data():
    with pytest.raises(ValueError, match="duplicate sale_id"):
        list_sales_history(principal(), [sale(72), sale(72, reference="OTHER-72")])


@pytest.mark.parametrize(
    ("page", "per_page"),
    [(0, 20), (1, 0), (True, 20), (1, 101), ("nope", 20)],
)
def test_reusable_pagination_validation_rejects_invalid_values(page, per_page):
    with pytest.raises(ValueError):
        validate_pagination(page, per_page)


def test_paginated_helper_rejects_non_sequence_inputs():
    with pytest.raises(ValueError, match="list or tuple"):
        paginate_read_only({"one": 1})
