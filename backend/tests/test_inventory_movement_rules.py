"""Tests for pure inventory movement planning; no database is used."""

from dataclasses import replace

import pytest

from inventory_movement_rules import (
    InventoryDisposition,
    MovementOrigin,
    MovementType,
    TrustedSystemOrigin,
    build_manual_adjustment_movement,
    build_purchase_receipt_movements,
    build_resalable_return_movements,
    build_sale_cancellation_movements,
    build_sale_movements,
    build_transfer_dispatch_movements,
    build_transfer_receipt_movements,
    plan_inventory_movements,
)
from permissions import (
    AuthenticatedPrincipal,
    INVENTORY_ADJUST,
    PURCHASES_MANAGE,
    PURCHASES_MANAGE_AUTHORIZED,
    RETURNS_INSPECT,
    SALE_CANCELLATION_APPROVAL,
    SALES_RECORD,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_DISPATCH,
    ResourceScope,
    Role,
)
from return_inspection_rules import (
    InspectionCondition,
    calculate_inspection_inventory_changes,
)
from return_rules import ReturnRequest, ReturnStatus
from sale_cancellation_rules import (
    Sale,
    SaleCancellationRequest,
    SaleCancellationStatus,
    SaleStatus,
)
from stock_adjustment_rules import AdjustmentDirection
from transfer_rules import TransferStatus


BUSINESS_ID = 10
SOURCE_BRANCH_ID = 22
DESTINATION_BRANCH_ID = 23
VARIANT_ID = 101
ACTOR_ID = 701


def principal(role=Role.BRANCH_MANAGER, branch_id=SOURCE_BRANCH_ID, permissions=None):
    if permissions is None:
        permissions = {
            Role.OWNER: {
                INVENTORY_ADJUST,
                PURCHASES_MANAGE,
                RETURNS_INSPECT,
                SALE_CANCELLATION_APPROVAL,
                SALES_RECORD,
                TRANSFERS_DISPATCH,
                TRANSFERS_CONFIRM_RECEIPT,
            },
            Role.BRANCH_MANAGER: {
                INVENTORY_ADJUST,
                PURCHASES_MANAGE_AUTHORIZED,
                RETURNS_INSPECT,
                SALE_CANCELLATION_APPROVAL,
                SALES_RECORD,
                TRANSFERS_DISPATCH,
                TRANSFERS_CONFIRM_RECEIPT,
            },
            Role.EMPLOYEE: {SALES_RECORD},
        }[role]
    return AuthenticatedPrincipal(
        role=role,
        business_id=BUSINESS_ID,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def scope(business_id=BUSINESS_ID, branch_id=None):
    return ResourceScope(business_id=business_id, branch_id=branch_id)


def branch_scope(branch_id=SOURCE_BRANCH_ID, business_id=BUSINESS_ID):
    return scope(business_id=business_id, branch_id=branch_id)


def variant_scopes(variant_ids=(VARIANT_ID,), business_id=BUSINESS_ID):
    return {
        variant_id: (scope(business_id), scope(business_id))
        for variant_id in variant_ids
    }


def sale(**changes):
    values = {
        "sale_id": 501,
        "business_id": BUSINESS_ID,
        "branch_id": SOURCE_BRANCH_ID,
        "status": SaleStatus.COMPLETED,
        "items": [{"product_variant_id": VARIANT_ID, "quantity": 4}],
        "has_processed_returns": False,
    }
    values.update(changes)
    return Sale(**values)


def inventory(branch_id=SOURCE_BRANCH_ID, variant_id=VARIANT_ID, available_qty=10):
    return {
        "branch_id": branch_id,
        "product_variant_id": variant_id,
        "available_qty": available_qty,
        "in_transit_qty": 50,
        "under_inspection_qty": 60,
        "damaged_qty": 70,
    }


def test_valid_sale_builds_a_deduction_and_expected_balance():
    movements = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    original = inventory()

    plan = plan_inventory_movements(
        movements, {(SOURCE_BRANCH_ID, VARIANT_ID): original}
    )

    assert movements[0].movement_type is MovementType.SALE
    assert movements[0].quantity_delta == -4
    assert movements[0].source_reference == "501"
    assert plan.expected_balances[0].available_qty_before == 10
    assert plan.expected_balances[0].expected_available_qty == 6
    assert original["available_qty"] == 10
    assert original["in_transit_qty"] == 50
    assert original["under_inspection_qty"] == 60
    assert original["damaged_qty"] == 70


def test_purchase_receipt_increases_available_only_for_sellable_received_items():
    movements = build_purchase_receipt_movements(
        principal(),
        actor_id=ACTOR_ID,
        purchase_id=601,
        receipt_id=602,
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 3,
                "disposition": InventoryDisposition.AVAILABLE,
            },
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 2,
                "disposition": InventoryDisposition.UNDER_INSPECTION,
            },
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 1,
                "disposition": InventoryDisposition.DAMAGED,
            },
        ],
    )
    plan = plan_inventory_movements(movements, {})

    assert len(movements) == 1
    assert movements[0].movement_type is MovementType.PURCHASE_RECEIPT
    assert movements[0].quantity_delta == 3
    assert plan.expected_balances[0].available_qty_before == 0
    assert plan.expected_balances[0].expected_available_qty == 3


def test_valid_manual_adjustment_reuses_authorized_adjustment_rules():
    movement = build_manual_adjustment_movement(
        principal(),
        actor_id=ACTOR_ID,
        branch_scope=branch_scope(),
        product_scope=scope(),
        variant_scope=scope(),
        product_variant_id=VARIANT_ID,
        inventory_record=inventory(available_qty=8),
        direction=AdjustmentDirection.DECREASE,
        quantity=3,
        reason="  Count correction  ",
        adjustment_reference=" count-2026-001 ",
    )[0]
    plan = plan_inventory_movements(
        (movement,), {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=8)}
    )

    assert movement.movement_type is MovementType.MANUAL_ADJUSTMENT
    assert movement.quantity_delta == -3
    assert movement.reason == "Count correction"
    assert movement.source_reference == "count-2026-001"
    assert plan.expected_balances[0].expected_available_qty == 5


def test_approved_sale_cancellation_proposes_restoration_once():
    cancellation = SaleCancellationRequest(
        sale_id=501,
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Duplicate charge.",
        status=SaleCancellationStatus.PENDING,
    )
    movements = build_sale_cancellation_movements(
        principal(),
        actor_id=ACTOR_ID,
        cancellation_request=cancellation,
        sale=sale(),
        existing_returns=[],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert movements[0].movement_type is MovementType.SALE_CANCELLATION
    assert movements[0].quantity_delta == 4
    assert movements[0].reason == "Duplicate charge."
    assert movements[0].effect_reference == (
        '[10,"SALE_CANCELLATION","501","SALE_CANCELLATION",22,101]'
    )
    approved_movements = build_sale_cancellation_movements(
        principal(),
        actor_id=ACTOR_ID,
        cancellation_request=replace(
            cancellation, status=SaleCancellationStatus.APPROVED
        ),
        sale=sale(),
        existing_returns=[],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    assert approved_movements == movements
    with pytest.raises(ValueError, match="already been processed"):
        plan_inventory_movements(
            movements,
            {(SOURCE_BRANCH_ID, VARIANT_ID): inventory()},
            existing_effect_references=[movements[0].effect_reference],
        )
    with pytest.raises(ValueError, match="Only completed sales"):
        build_sale_cancellation_movements(
            principal(),
            actor_id=ACTOR_ID,
            cancellation_request=cancellation,
            sale=sale(status=SaleStatus.CANCELLED),
            existing_returns=[],
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
        )


def test_cancellation_movement_subtracts_all_inspected_dispositions():
    return_request = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Customer return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 3}],
        original_sale_id=501,
        status=ReturnStatus.PARTIALLY_INSPECTED,
    )
    returned_items = [
        {"return_item_id": 701 + offset, "product_variant_id": VARIANT_ID, "quantity": 1}
        for offset in range(3)
    ]
    inspection_items = [
        {
            "return_item_id": 701,
            "quantity": 1,
            "condition": InspectionCondition.RESALABLE,
        },
        {
            "return_item_id": 702,
            "quantity": 1,
            "condition": InspectionCondition.DAMAGED,
        },
        {
            "return_item_id": 703,
            "quantity": 1,
            "condition": InspectionCondition.UNDER_INSPECTION,
        },
    ]
    inspection_changes = calculate_inspection_inventory_changes(
        principal(),
        return_request,
        returned_items,
        inspection_id=804,
        inspection_items=inspection_items,
    )
    movements = build_sale_cancellation_movements(
        principal(),
        actor_id=ACTOR_ID,
        cancellation_request=SaleCancellationRequest(
            sale_id=501,
            business_id=BUSINESS_ID,
            branch_id=SOURCE_BRANCH_ID,
            reason="Duplicate sale.",
        ),
        sale=sale(items=[{"product_variant_id": VARIANT_ID, "quantity": 5}]),
        existing_returns=[return_request],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert inspection_changes.available_stock_additions == {VARIANT_ID: 1}
    assert inspection_changes.damaged_stock_additions == {VARIANT_ID: 1}
    assert inspection_changes.remaining_under_inspection == {703: 1}
    assert len(movements) == 1
    assert movements[0].branch_id == SOURCE_BRANCH_ID
    assert movements[0].quantity_delta == 2


def test_fully_returned_sale_has_no_zero_quantity_cancellation_movement():
    returned = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Customer return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 4}],
        original_sale_id=501,
        status=ReturnStatus.PROCESSED,
    )
    movements = build_sale_cancellation_movements(
        principal(),
        actor_id=ACTOR_ID,
        cancellation_request=SaleCancellationRequest(
            sale_id=501,
            business_id=BUSINESS_ID,
            branch_id=SOURCE_BRANCH_ID,
            reason="Duplicate sale.",
        ),
        sale=sale(),
        existing_returns=[returned],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert movements == ()
    assert plan_inventory_movements(movements, {}).expected_balances == ()


def test_duplicate_sale_lines_produce_one_branch_variant_cancellation_effect():
    returned = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Customer return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        original_sale_id=501,
        status=ReturnStatus.PROCESSED,
    )
    movements = build_sale_cancellation_movements(
        principal(),
        actor_id=ACTOR_ID,
        cancellation_request=SaleCancellationRequest(
            sale_id=501,
            business_id=BUSINESS_ID,
            branch_id=SOURCE_BRANCH_ID,
            reason="Duplicate sale.",
        ),
        sale=sale(
            items=[
                {"product_variant_id": VARIANT_ID, "quantity": 2},
                {"product_variant_id": VARIANT_ID, "quantity": 3},
            ]
        ),
        existing_returns=[returned],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert len(movements) == 1
    assert movements[0].branch_id == SOURCE_BRANCH_ID
    assert movements[0].product_variant_id == VARIANT_ID
    assert movements[0].quantity_delta == 4


def test_zero_restoration_still_requires_cancellation_approval_permission():
    returned = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Customer return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 4}],
        original_sale_id=501,
        status=ReturnStatus.PROCESSED,
    )
    with pytest.raises(PermissionError):
        build_sale_cancellation_movements(
            principal(Role.EMPLOYEE),
            actor_id=ACTOR_ID,
            cancellation_request=SaleCancellationRequest(
                sale_id=501,
                business_id=BUSINESS_ID,
                branch_id=SOURCE_BRANCH_ID,
                reason="Duplicate sale.",
                status=SaleCancellationStatus.APPROVED,
            ),
            sale=sale(),
            existing_returns=[returned],
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
        )


def test_return_inspection_credits_resalable_but_not_damaged_or_uninspected():
    request = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Customer return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 4}],
        original_sale_id=501,
        status=ReturnStatus.PENDING,
    )
    returned_items = [
        {"return_item_id": 701, "product_variant_id": VARIANT_ID, "quantity": 4}
    ]
    inspection_items = [
        {
            "return_item_id": 701,
            "quantity": 2,
            "condition": InspectionCondition.RESALABLE,
        },
        {
            "return_item_id": 701,
            "quantity": 1,
            "condition": InspectionCondition.DAMAGED,
        },
        {
            "return_item_id": 701,
            "quantity": 1,
            "condition": InspectionCondition.UNDER_INSPECTION,
        },
    ]

    movements = build_resalable_return_movements(
        principal(),
        actor_id=ACTOR_ID,
        return_request=request,
        returned_items=returned_items,
        inspection_id=801,
        inspection_items=inspection_items,
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    plan = plan_inventory_movements(
        movements, {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=5)}
    )

    assert len(movements) == 1
    assert movements[0].movement_type is MovementType.RETURN_RESTOCK
    assert movements[0].quantity_delta == 2
    assert plan.expected_balances[0].expected_available_qty == 7


def test_return_inspection_with_no_resalable_items_has_no_available_movement():
    request = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Damaged return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        original_sale_id=501,
    )
    movements = build_resalable_return_movements(
        principal(),
        actor_id=ACTOR_ID,
        return_request=request,
        returned_items=[
            {"return_item_id": 701, "product_variant_id": VARIANT_ID, "quantity": 1}
        ],
        inspection_id=802,
        inspection_items=[
            {
                "return_item_id": 701,
                "quantity": 1,
                "condition": InspectionCondition.DAMAGED,
            }
        ],
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert movements == ()
    assert plan_inventory_movements(movements, {}).expected_balances == ()


def test_foreign_variant_is_rejected_even_when_inspection_marks_it_damaged():
    request = ReturnRequest(
        business_id=BUSINESS_ID,
        branch_id=SOURCE_BRANCH_ID,
        reason="Damaged return.",
        items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        original_sale_id=501,
    )
    with pytest.raises(PermissionError):
        build_resalable_return_movements(
            principal(),
            actor_id=ACTOR_ID,
            return_request=request,
            returned_items=[
                {"return_item_id": 701, "product_variant_id": VARIANT_ID, "quantity": 1}
            ],
            inspection_id=803,
            inspection_items=[
                {
                    "return_item_id": 701,
                    "quantity": 1,
                    "condition": InspectionCondition.DAMAGED,
                }
            ],
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(business_id=99),
        )


def test_transfer_dispatch_deducts_source_and_receipt_credits_only_destination():
    dispatch = build_transfer_dispatch_movements(
        principal(),
        actor_id=ACTOR_ID,
        transfer_id=901,
        current_status=TransferStatus.PENDING,
        source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
        destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
        variant_scopes=variant_scopes(),
        items=[{"product_variant_id": VARIANT_ID, "quantity": 4}],
    )
    receipt = build_transfer_receipt_movements(
        principal(branch_id=DESTINATION_BRANCH_ID),
        actor_id=ACTOR_ID,
        transfer_id=901,
        receipt_id=902,
        current_status=TransferStatus.IN_TRANSIT,
        source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
        destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 2,
                "disposition": InventoryDisposition.AVAILABLE,
            },
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 1,
                "disposition": InventoryDisposition.DAMAGED,
            },
        ],
        dispatched_items=[{"product_variant_id": VARIANT_ID, "quantity": 4}],
        complete_transfer=True,
    )

    dispatch_plan = plan_inventory_movements(
        dispatch, {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=6)}
    )
    receipt_plan = plan_inventory_movements(
        receipt, {(DESTINATION_BRANCH_ID, VARIANT_ID): inventory(DESTINATION_BRANCH_ID, available_qty=1)}
    )

    assert dispatch[0].movement_type is MovementType.TRANSFER_DISPATCH
    assert dispatch[0].quantity_delta == -4
    assert dispatch_plan.expected_balances[0].expected_available_qty == 2
    assert receipt[0].movement_type is MovementType.TRANSFER_RECEIPT
    assert receipt[0].branch_id == DESTINATION_BRANCH_ID
    assert receipt[0].quantity_delta == 2
    assert receipt_plan.expected_balances[0].expected_available_qty == 3


def test_non_available_transfer_receipt_does_not_credit_destination():
    movements = build_transfer_receipt_movements(
        principal(branch_id=DESTINATION_BRANCH_ID),
        actor_id=ACTOR_ID,
        transfer_id=901,
        receipt_id=903,
        current_status=TransferStatus.IN_TRANSIT,
        source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
        destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 2,
                "disposition": InventoryDisposition.UNDER_INSPECTION,
            }
        ],
        dispatched_items=[{"product_variant_id": VARIANT_ID, "quantity": 3}],
    )

    assert movements == ()


def test_insufficient_available_stock_rejects_sale_and_transfer_dispatch():
    sale_effects = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(items=[{"product_variant_id": VARIANT_ID, "quantity": 5}]),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    with pytest.raises(ValueError, match="exceeds available stock"):
        plan_inventory_movements(
            sale_effects,
            {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=4)},
        )

    transfer_effects = build_transfer_dispatch_movements(
        principal(),
        actor_id=ACTOR_ID,
        transfer_id=904,
        current_status=TransferStatus.PENDING,
        source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
        destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
        variant_scopes=variant_scopes(),
        items=[{"product_variant_id": VARIANT_ID, "quantity": 11}],
    )
    with pytest.raises(ValueError, match="exceeds available stock"):
        plan_inventory_movements(
            transfer_effects,
            {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=10)},
        )


@pytest.mark.parametrize(
    "bad_items",
    [
        [{"product_variant_id": True, "quantity": 1}],
        [{"product_variant_id": VARIANT_ID, "quantity": 0}],
        [{"product_variant_id": VARIANT_ID, "quantity": 1.5}],
        [{"product_variant_id": VARIANT_ID, "quantity": 1, "disposition": "UNKNOWN"}],
    ],
)
def test_invalid_receipt_quantities_identifiers_or_dispositions_are_rejected(bad_items):
    with pytest.raises(ValueError):
        build_purchase_receipt_movements(
            principal(),
            actor_id=ACTOR_ID,
            purchase_id=601,
            receipt_id=602,
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
            receipt_items=bad_items,
        )


@pytest.mark.parametrize("purchase_id,receipt_id,actor_id", [(True, 2, ACTOR_ID), (1, 0, ACTOR_ID), (1, 2, False)])
def test_invalid_purchase_or_actor_identifiers_are_rejected(purchase_id, receipt_id, actor_id):
    with pytest.raises(ValueError):
        build_purchase_receipt_movements(
            principal(),
            actor_id=actor_id,
            purchase_id=purchase_id,
            receipt_id=receipt_id,
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
            receipt_items=[
                {
                    "product_variant_id": VARIANT_ID,
                    "quantity": 1,
                    "disposition": "AVAILABLE",
                }
            ],
        )


@pytest.mark.parametrize("record", [
    {"branch_id": SOURCE_BRANCH_ID, "product_variant_id": VARIANT_ID},
    inventory(available_qty=True),
    inventory(available_qty=-1),
    inventory(branch_id=DESTINATION_BRANCH_ID),
    inventory(variant_id=202),
])
def test_malformed_or_mismatched_inventory_records_are_rejected(record):
    movement = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    with pytest.raises(ValueError):
        plan_inventory_movements(
            movement, {(SOURCE_BRANCH_ID, VARIANT_ID): record}
        )


def test_absent_inventory_record_is_zero_for_increase_but_not_for_sale():
    purchase = build_purchase_receipt_movements(
        principal(),
        actor_id=ACTOR_ID,
        purchase_id=611,
        receipt_id=612,
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 5,
                "disposition": "AVAILABLE",
            }
        ],
    )
    purchase_plan = plan_inventory_movements(purchase, {})
    sale_effects = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert purchase_plan.expected_balances[0].available_qty_before == 0
    assert purchase_plan.expected_balances[0].expected_available_qty == 5
    with pytest.raises(ValueError, match="exceeds available stock"):
        plan_inventory_movements(sale_effects, {})


def test_unauthorized_business_branch_and_purchase_access_are_denied():
    with pytest.raises(PermissionError):
        build_sale_movements(
            principal(branch_id=DESTINATION_BRANCH_ID),
            actor_id=ACTOR_ID,
            sale=sale(),
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
        )
    with pytest.raises(PermissionError):
        build_sale_movements(
            principal(Role.OWNER),
            actor_id=ACTOR_ID,
            sale=sale(),
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(business_id=99),
        )
    with pytest.raises(PermissionError):
        build_purchase_receipt_movements(
            principal(Role.BRANCH_MANAGER, permissions=set()),
            actor_id=ACTOR_ID,
            purchase_id=601,
            receipt_id=602,
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
            receipt_items=[
                {
                    "product_variant_id": VARIANT_ID,
                    "quantity": 1,
                    "disposition": "AVAILABLE",
                }
            ],
        )


@pytest.mark.parametrize("dependency_index", [0, 1])
def test_inventory_movements_require_business_wide_product_and_variant_scopes(
    dependency_index,
):
    scopes = list(variant_scopes()[VARIANT_ID])
    scopes[dependency_index] = scope(branch_id=DESTINATION_BRANCH_ID)

    with pytest.raises(ValueError, match="must be business-wide"):
        build_sale_movements(
            principal(),
            actor_id=ACTOR_ID,
            sale=sale(),
            branch_scope=branch_scope(),
            variant_scopes={VARIANT_ID: tuple(scopes)},
        )


def test_transfer_dispatch_and_receipt_require_access_at_their_respective_branch():
    args = {
        "actor_id": ACTOR_ID,
        "transfer_id": 920,
        "source_branch_scope": branch_scope(SOURCE_BRANCH_ID),
        "destination_branch_scope": branch_scope(DESTINATION_BRANCH_ID),
        "variant_scopes": variant_scopes(),
    }
    with pytest.raises(PermissionError):
        build_transfer_dispatch_movements(
            principal(branch_id=DESTINATION_BRANCH_ID),
            current_status=TransferStatus.PENDING,
            items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
            **args,
        )
    with pytest.raises(PermissionError):
        build_transfer_receipt_movements(
            principal(branch_id=SOURCE_BRANCH_ID),
            receipt_id=921,
            current_status=TransferStatus.IN_TRANSIT,
            receipt_items=[
                {
                    "product_variant_id": VARIANT_ID,
                    "quantity": 1,
                    "disposition": "AVAILABLE",
                }
            ],
            dispatched_items=[{"product_variant_id": VARIANT_ID, "quantity": 2}],
            **args,
        )


@pytest.mark.parametrize("dependency_index", [0, 1])
def test_transfer_movements_reject_branch_scoped_product_or_variant(
    dependency_index,
):
    scopes = list(variant_scopes()[VARIANT_ID])
    scopes[dependency_index] = scope(branch_id=DESTINATION_BRANCH_ID)

    with pytest.raises(ValueError, match="must be business-wide"):
        build_transfer_dispatch_movements(
            principal(),
            actor_id=ACTOR_ID,
            transfer_id=930,
            current_status=TransferStatus.PENDING,
            source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
            destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
            variant_scopes={VARIANT_ID: tuple(scopes)},
            items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        )


def test_duplicate_effects_in_one_plan_and_prior_history_are_rejected():
    movements = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    records = {(SOURCE_BRANCH_ID, VARIANT_ID): inventory()}

    with pytest.raises(ValueError, match="already been processed"):
        plan_inventory_movements(movements + movements, records)
    with pytest.raises(ValueError, match="already been processed"):
        plan_inventory_movements(
            movements,
            records,
            existing_effect_references=[movements[0].effect_reference],
        )


def test_effects_for_the_same_balance_are_applied_cumulatively_in_order():
    sale_effects = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(items=[{"product_variant_id": VARIANT_ID, "quantity": 6}]),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    purchase_effects = build_purchase_receipt_movements(
        principal(),
        actor_id=ACTOR_ID,
        purchase_id=630,
        receipt_id=631,
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 5,
                "disposition": "AVAILABLE",
            }
        ],
    )
    plan = plan_inventory_movements(
        sale_effects + purchase_effects,
        {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=10)},
    )

    assert len(plan.expected_balances) == 1
    assert plan.expected_balances[0].available_qty_before == 10
    assert plan.expected_balances[0].expected_available_qty == 9


def test_cumulative_effect_cannot_go_negative_even_if_a_later_increase_would_cover_it():
    sale_effects = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(items=[{"product_variant_id": VARIANT_ID, "quantity": 11}]),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )
    purchase_effects = build_purchase_receipt_movements(
        principal(),
        actor_id=ACTOR_ID,
        purchase_id=640,
        receipt_id=641,
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
        receipt_items=[
            {
                "product_variant_id": VARIANT_ID,
                "quantity": 20,
                "disposition": "AVAILABLE",
            }
        ],
    )

    with pytest.raises(ValueError, match="exceeds available stock"):
        plan_inventory_movements(
            sale_effects + purchase_effects,
            {(SOURCE_BRANCH_ID, VARIANT_ID): inventory(available_qty=10)},
        )


def test_system_origin_is_supported_as_trusted_movement_context():
    movements = build_sale_movements(
        None,
        actor_id=None,
        system_origin=TrustedSystemOrigin("trusted.sale_import"),
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )

    assert movements[0].origin is MovementOrigin.SYSTEM
    assert movements[0].actor_id is None
    assert movements[0].system_origin == "trusted.sale_import"


def test_planner_rejects_malformed_effect_identifiers_and_wrong_business_batches():
    movement = build_sale_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale=sale(),
        branch_scope=branch_scope(),
        variant_scopes=variant_scopes(),
    )[0]

    with pytest.raises(ValueError, match="business_id"):
        plan_inventory_movements((replace(movement, business_id=True),), {})
    with pytest.raises(ValueError, match="one business"):
        plan_inventory_movements(
            (movement, replace(movement, business_id=99, source_reference="other")), {}
        )


def test_receipt_requires_disposition_and_transfer_receipt_cannot_exceed_dispatch():
    with pytest.raises(ValueError, match="disposition"):
        build_purchase_receipt_movements(
            principal(),
            actor_id=ACTOR_ID,
            purchase_id=650,
            receipt_id=651,
            branch_scope=branch_scope(),
            variant_scopes=variant_scopes(),
            receipt_items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        )
    with pytest.raises(ValueError, match="exceed"):
        build_transfer_receipt_movements(
            principal(branch_id=DESTINATION_BRANCH_ID),
            actor_id=ACTOR_ID,
            transfer_id=652,
            receipt_id=653,
            current_status=TransferStatus.IN_TRANSIT,
            source_branch_scope=branch_scope(SOURCE_BRANCH_ID),
            destination_branch_scope=branch_scope(DESTINATION_BRANCH_ID),
            variant_scopes=variant_scopes(),
            receipt_items=[
                {
                    "product_variant_id": VARIANT_ID,
                    "quantity": 2,
                    "disposition": "AVAILABLE",
                }
            ],
            dispatched_items=[{"product_variant_id": VARIANT_ID, "quantity": 1}],
        )


def test_invalid_manual_adjustment_quantity_or_missing_reference_is_rejected():
    common = {
        "principal": principal(),
        "actor_id": ACTOR_ID,
        "branch_scope": branch_scope(),
        "product_scope": scope(),
        "variant_scope": scope(),
        "product_variant_id": VARIANT_ID,
        "inventory_record": inventory(),
        "direction": AdjustmentDirection.INCREASE,
        "reason": "Stock correction",
        "adjustment_reference": "manual-1",
    }
    with pytest.raises(ValueError, match="positive integer"):
        build_manual_adjustment_movement(quantity=True, **common)
    with pytest.raises(ValueError, match="required"):
        build_manual_adjustment_movement(
            quantity=1, **{**common, "adjustment_reference": None}
        )
