"""Tests for pure branch-variant stock availability rules."""

import pytest

from permissions import (
    AuthenticatedPrincipal,
    INVENTORY_ADJUST,
    INVENTORY_VIEW,
    Role,
    ResourceScope,
)
from stock_rules import (
    StockStatus,
    calculate_remaining_quantity,
    can_access_branch_variant_stock,
    classify_stock_status,
    get_available_quantity,
    is_stock_available,
    require_branch_variant_stock_access,
    validate_available_quantity,
    validate_minimum_stock_level,
    validate_requested_quantity,
    validate_stock_availability,
)


def make_principal(role=Role.BRANCH_MANAGER, business_id=10, branch_id=22, permissions=None):
    if permissions is None:
        permissions = {INVENTORY_VIEW, INVENTORY_ADJUST}
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def scope(business_id=10, branch_id=None):
    return ResourceScope(business_id=business_id, branch_id=branch_id)


def inventory_record(branch_id=22, product_variant_id=101, available_qty=8, **other_quantities):
    return {
        "branch_id": branch_id,
        "product_variant_id": product_variant_id,
        "available_qty": available_qty,
        **other_quantities,
    }


def test_available_quantity_uses_only_the_matching_available_qty():
    record = inventory_record(
        available_qty=5,
        in_transit_qty=20,
        under_inspection_qty=4,
        damaged_qty=9,
    )

    assert get_available_quantity(record, 22, 101) == 5


def test_missing_branch_variant_inventory_record_has_zero_available_quantity():
    assert get_available_quantity(None, 22, 101) == 0


def test_stock_availability_and_remaining_quantity_at_normal_levels():
    assert is_stock_available(8, 3)
    assert not is_stock_available(2, 3)
    assert validate_stock_availability(8, 3)
    assert calculate_remaining_quantity(8, 3) == 5
    assert calculate_remaining_quantity(3, 3) == 0


def test_insufficient_stock_cannot_be_sold_or_deducted():
    with pytest.raises(ValueError, match="exceeds available stock"):
        validate_stock_availability(2, 3)
    with pytest.raises(ValueError, match="exceeds available stock"):
        calculate_remaining_quantity(2, 3)
    with pytest.raises(ValueError, match="exceeds available stock"):
        calculate_remaining_quantity(0, 1)


@pytest.mark.parametrize(
    ("available_qty", "minimum_stock_level", "expected"),
    [
        (0, 5, StockStatus.OUT_OF_STOCK),
        (1, 5, StockStatus.LOW_STOCK),
        (5, 5, StockStatus.LOW_STOCK),
        (6, 5, StockStatus.IN_STOCK),
        (1, 0, StockStatus.IN_STOCK),
    ],
)
def test_stock_status_covers_zero_low_and_in_stock(available_qty, minimum_stock_level, expected):
    assert classify_stock_status(available_qty, minimum_stock_level) is expected


def test_minimum_level_is_independently_configurable_for_each_branch_variant():
    assert classify_stock_status(4, 3) is StockStatus.IN_STOCK
    assert classify_stock_status(4, 4) is StockStatus.LOW_STOCK


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "2", None])
def test_available_quantity_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_available_quantity(value)


@pytest.mark.parametrize("value", [True, False, -1, 1.5, "2", None])
def test_minimum_stock_level_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_minimum_stock_level(value)


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "2", None])
def test_requested_sale_or_deduction_quantity_must_be_positive_integer(value):
    with pytest.raises(ValueError):
        validate_requested_quantity(value)


def test_existing_malformed_inventory_records_are_not_treated_as_missing():
    with pytest.raises(ValueError, match="available_qty"):
        get_available_quantity({"branch_id": 22, "product_variant_id": 101}, 22, 101)
    with pytest.raises(ValueError, match="available_qty"):
        get_available_quantity(inventory_record(available_qty=True), 22, 101)
    with pytest.raises(ValueError, match="available_qty"):
        get_available_quantity(inventory_record(available_qty=-1), 22, 101)


def test_stock_quantity_is_isolated_by_both_branch_and_variant():
    records = {
        (22, 101): inventory_record(22, 101, 7),
        (22, 202): inventory_record(22, 202, 2),
        (23, 101): inventory_record(23, 101, 11),
    }

    assert get_available_quantity(records[(22, 101)], 22, 101) == 7
    assert get_available_quantity(records[(22, 202)], 22, 202) == 2
    assert get_available_quantity(records[(23, 101)], 23, 101) == 11
    with pytest.raises(ValueError, match="does not match"):
        get_available_quantity(records[(23, 101)], 22, 101)
    with pytest.raises(ValueError, match="does not match"):
        get_available_quantity(records[(22, 202)], 22, 101)


def test_stock_access_reuses_branch_business_and_variant_isolation_rules():
    manager = make_principal()
    branch_22 = scope(branch_id=22)
    variant_same_business = scope()

    assert can_access_branch_variant_stock(manager, branch_22, variant_same_business)
    assert not can_access_branch_variant_stock(
        manager, scope(branch_id=23), variant_same_business
    )
    assert not can_access_branch_variant_stock(
        manager, branch_22, scope(business_id=99)
    )
    assert not can_access_branch_variant_stock(
        make_principal(permissions=set()), branch_22, variant_same_business
    )
    assert can_access_branch_variant_stock(
        make_principal(Role.OWNER), scope(branch_id=23), variant_same_business
    )
    assert not can_access_branch_variant_stock(
        make_principal(Role.OWNER), scope(), variant_same_business
    )

    with pytest.raises(PermissionError):
        require_branch_variant_stock_access(
            manager, scope(branch_id=23), variant_same_business
        )
    with pytest.raises(PermissionError):
        require_branch_variant_stock_access(
            make_principal(Role.OWNER), scope(), variant_same_business
        )


def test_stock_access_rejects_a_branch_scoped_variant_resource():
    manager = make_principal()
    owner = make_principal(Role.OWNER)
    branch_22 = scope(branch_id=22)
    branch_scoped_variant = scope(branch_id=23)

    assert not can_access_branch_variant_stock(
        manager, branch_22, branch_scoped_variant
    )
    assert not can_access_branch_variant_stock(
        owner, branch_22, branch_scoped_variant
    )
    with pytest.raises(PermissionError):
        require_branch_variant_stock_access(
            manager, branch_22, branch_scoped_variant
        )


def test_stock_access_respects_role_permissions_for_deductions():
    manager = make_principal()
    assert can_access_branch_variant_stock(
        manager,
        scope(branch_id=22),
        scope(),
        required_permission=INVENTORY_ADJUST,
    )
    employee = make_principal(Role.EMPLOYEE, permissions={INVENTORY_ADJUST})
    assert not can_access_branch_variant_stock(
        employee,
        scope(branch_id=22),
        scope(),
        required_permission=INVENTORY_ADJUST,
    )
