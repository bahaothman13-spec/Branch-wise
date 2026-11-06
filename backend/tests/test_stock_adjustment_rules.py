"""Tests for pure manual stock-adjustment business rules."""

import pytest

from permissions import AuthenticatedPrincipal, INVENTORY_ADJUST, Role, ResourceScope
from stock_adjustment_rules import (
    AdjustmentDirection,
    InventoryMovementDraft,
    StockAdjustmentResult,
    validate_adjustment_reference,
    validate_stock_adjustment,
)


def make_principal(
    role=Role.BRANCH_MANAGER, business_id=10, branch_id=22, permissions=None
):
    if permissions is None:
        permissions = {INVENTORY_ADJUST}
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def scope(business_id=10, branch_id=None):
    return ResourceScope(business_id=business_id, branch_id=branch_id)


def inventory_record(branch_id=22, product_variant_id=101, available_qty=8):
    return {
        "branch_id": branch_id,
        "product_variant_id": product_variant_id,
        "available_qty": available_qty,
    }


def adjust(**changes):
    values = {
        "principal": make_principal(),
        "actor_id": 701,
        "branch_scope": scope(branch_id=22),
        "product_scope": scope(),
        "variant_scope": scope(),
        "product_variant_id": 101,
        "inventory_record": inventory_record(),
        "direction": AdjustmentDirection.INCREASE,
        "quantity": 3,
        "reason": "  Opening balance correction  ",
    }
    values.update(changes)
    return validate_stock_adjustment(**values)


def test_valid_stock_increase_returns_proposed_effect_without_mutating_inventory():
    inventory = inventory_record(available_qty=8)
    original = inventory.copy()

    result = adjust(inventory_record=inventory)

    assert result == StockAdjustmentResult(
        available_qty_before=8,
        quantity_change=3,
        expected_available_qty=11,
        movement=InventoryMovementDraft(
            actor_id=701,
            business_id=10,
            branch_id=22,
            product_variant_id=101,
            direction=AdjustmentDirection.INCREASE,
            quantity=3,
            reason="Opening balance correction",
        ),
    )
    assert inventory == original


def test_valid_stock_decrease_returns_signed_effect():
    result = adjust(
        direction=AdjustmentDirection.DECREASE,
        quantity=5,
        inventory_record=inventory_record(available_qty=8),
    )

    assert result.quantity_change == -5
    assert result.expected_available_qty == 3
    assert result.movement.direction is AdjustmentDirection.DECREASE
    assert result.movement.quantity == 5


def test_decrease_cannot_exceed_available_stock_or_make_it_negative():
    with pytest.raises(ValueError, match="exceeds available stock"):
        adjust(
            direction="DECREASE",
            quantity=9,
            inventory_record=inventory_record(available_qty=8),
        )


def test_missing_inventory_record_is_zero_and_authorized_increase_is_allowed():
    result = adjust(inventory_record=None, quantity=4)

    assert result.available_qty_before == 0
    assert result.quantity_change == 4
    assert result.expected_available_qty == 4


@pytest.mark.parametrize("quantity", [0, -1, True, False, 1.5, "2", None])
def test_adjustment_quantity_must_be_a_positive_integer(quantity):
    with pytest.raises(ValueError, match="positive integer"):
        adjust(quantity=quantity)


@pytest.mark.parametrize("reason", [None, "", "   ", 42, False])
def test_adjustment_reason_must_be_meaningful_non_empty_text(reason):
    with pytest.raises(ValueError, match="reason"):
        adjust(reason=reason)


def test_employee_and_principal_without_adjust_permission_are_denied():
    employee = make_principal(Role.EMPLOYEE, permissions={INVENTORY_ADJUST})
    without_grant = make_principal(permissions=set())

    with pytest.raises(PermissionError):
        adjust(principal=employee)
    with pytest.raises(PermissionError):
        adjust(principal=without_grant)


def test_cross_business_branch_and_variant_access_are_denied():
    with pytest.raises(PermissionError):
        adjust(branch_scope=scope(business_id=99, branch_id=22))
    with pytest.raises(PermissionError):
        adjust(product_scope=scope(business_id=99))
    with pytest.raises(PermissionError):
        adjust(variant_scope=scope(business_id=99))
    with pytest.raises(PermissionError):
        adjust(branch_scope=scope(branch_id=23))


def test_invalid_trusted_resource_context_is_rejected():
    with pytest.raises(ValueError, match="resource context"):
        adjust(branch_scope=object())
    with pytest.raises(ValueError, match="resource context"):
        adjust(branch_scope=scope(branch_id=None))
    with pytest.raises(PermissionError):
        adjust(principal=object())
    with pytest.raises(ValueError, match="product_variant_id"):
        adjust(product_variant_id=True)


@pytest.mark.parametrize(
    "record",
    [
        {"branch_id": 22, "product_variant_id": 101},
        inventory_record(available_qty=True),
        inventory_record(available_qty=-1),
        inventory_record(branch_id=23),
        inventory_record(product_variant_id=202),
    ],
)
def test_existing_malformed_or_mismatched_inventory_records_are_rejected(record):
    with pytest.raises(ValueError):
        adjust(inventory_record=record)


def test_adjustment_reference_is_trimmed_and_duplicate_is_rejected():
    result = adjust(
        adjustment_reference="  manual-2026-0001 ",
        existing_adjustment_references=["manual-2026-0002"],
    )
    assert result.movement.adjustment_reference == "manual-2026-0001"

    with pytest.raises(ValueError, match="already been processed"):
        adjust(
            adjustment_reference=" manual-2026-0001 ",
            existing_adjustment_references={"manual-2026-0001"},
        )


def test_adjustment_reference_validation_rejects_invalid_inputs_and_collections():
    for reference in ("", "  ", 4):
        with pytest.raises(ValueError):
            validate_adjustment_reference(reference)
    with pytest.raises(ValueError, match="collection"):
        validate_adjustment_reference("reference-1", "reference-1")
    with pytest.raises(ValueError, match="non-empty strings"):
        validate_adjustment_reference("reference-1", [None])


def test_adjustment_reference_is_optional_and_never_generated():
    result = adjust()

    assert result.movement.adjustment_reference is None
