"""Unit tests for pure returned-product inspection rules."""

import pytest

from permissions import AuthenticatedPrincipal, RETURNS_INSPECT, Role
from return_inspection_rules import (
    InspectionCondition,
    ReturnInspection,
    ReturnInspectionItem,
    calculate_inspection_inventory_changes,
    calculate_remaining_under_inspection_quantities,
    validate_return_inspection,
)
from return_rules import ReturnRequest, ReturnStatus


def make_principal(role=Role.BRANCH_MANAGER, business_id=10, branch_id=22, permissions=None):
    if permissions is None:
        permissions = {RETURNS_INSPECT}
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def make_return(**changes):
    values = {
        "business_id": 10,
        "branch_id": 22,
        "reason": "Customer returned the products.",
        "items": [
            {"product_variant_id": 101, "quantity": 3},
            {"product_variant_id": 202, "quantity": 2},
        ],
        "original_sale_id": 501,
        "status": ReturnStatus.PENDING,
    }
    values.update(changes)
    return ReturnRequest(**values)


RETURNED_ITEMS = [
    {"return_item_id": 701, "product_variant_id": 101, "quantity": 3},
    {"return_item_id": 702, "product_variant_id": 202, "quantity": 2},
]


def test_owner_and_branch_manager_can_inspect_in_scope():
    request = make_return()
    inspected = [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}]

    manager_result = validate_return_inspection(
        make_principal(), request, RETURNED_ITEMS, 801, inspected
    )
    owner_result = validate_return_inspection(
        make_principal(Role.OWNER), request, RETURNED_ITEMS, 802, inspected
    )

    assert manager_result.items == (
        ReturnInspectionItem(701, 1, InspectionCondition.RESALABLE),
    )
    assert owner_result.items == manager_result.items


def test_employee_cannot_inspect_even_if_given_a_trusted_inspect_grant():
    with pytest.raises(PermissionError, match="Owner or Branch Manager"):
        validate_return_inspection(
            make_principal(Role.EMPLOYEE, permissions={RETURNS_INSPECT}),
            make_return(),
            RETURNED_ITEMS,
            801,
            [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}],
        )


def test_inspection_requires_returns_inspect_permission_and_matching_scope():
    args = (
        make_return(),
        RETURNED_ITEMS,
        801,
        [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}],
    )
    with pytest.raises(PermissionError):
        validate_return_inspection(make_principal(permissions=set()), *args)
    with pytest.raises(PermissionError):
        validate_return_inspection(
            make_principal(Role.OWNER, business_id=99), *args
        )
    with pytest.raises(PermissionError):
        validate_return_inspection(
            make_principal(branch_id=23), *args
        )


def test_unlinked_return_must_be_verified_before_inspection():
    request = make_return(original_sale_id=None)
    with pytest.raises(ValueError, match="verified before inspection"):
        validate_return_inspection(
            make_principal(),
            request,
            RETURNED_ITEMS,
            801,
            [{"return_item_id": 701, "quantity": 1, "condition": "DAMAGED"}],
        )

    verified = make_return(original_sale_id=None, status=ReturnStatus.VERIFIED)
    assert validate_return_inspection(
        make_principal(),
        verified,
        RETURNED_ITEMS,
        801,
        [{"return_item_id": 701, "quantity": 1, "condition": "DAMAGED"}],
    )


def test_partial_inspections_accumulate_without_exceeding_return_item_quantity():
    history = [
        ReturnInspection(
            801,
            (ReturnInspectionItem(701, 1, InspectionCondition.RESALABLE),),
        )
    ]
    result = validate_return_inspection(
        make_principal(),
        make_return(),
        RETURNED_ITEMS,
        802,
        [{"return_item_id": 701, "quantity": 2, "condition": "DAMAGED"}],
        inspection_history=history,
    )

    assert result.items[0].quantity == 2
    with pytest.raises(ValueError, match="remaining returned quantity"):
        validate_return_inspection(
            make_principal(),
            make_return(),
            RETURNED_ITEMS,
            803,
            [{"return_item_id": 701, "quantity": 3, "condition": "DAMAGED"}],
            inspection_history=history,
        )


def test_under_inspection_quantities_remain_pending_and_can_be_classified_later():
    pending = ReturnInspection(
        801,
        (ReturnInspectionItem(701, 2, InspectionCondition.UNDER_INSPECTION),),
    )
    assert calculate_remaining_under_inspection_quantities(
        make_return(), RETURNED_ITEMS, [pending]
    ) == {701: 3, 702: 2}

    final = validate_return_inspection(
        make_principal(),
        make_return(),
        RETURNED_ITEMS,
        802,
        [{"return_item_id": 701, "quantity": 2, "condition": "RESALABLE"}],
        inspection_history=[pending],
    )
    assert final.items[0].condition is InspectionCondition.RESALABLE


def test_damaged_and_resalable_quantities_have_separate_expected_stock_effects():
    result = calculate_inspection_inventory_changes(
        make_principal(),
        make_return(),
        RETURNED_ITEMS,
        801,
        [
            {"return_item_id": 701, "quantity": 2, "condition": "RESALABLE"},
            {"return_item_id": 701, "quantity": 1, "condition": "DAMAGED"},
            {"return_item_id": 702, "quantity": 2, "condition": "UNDER_INSPECTION"},
        ],
    )

    assert result.available_stock_additions == {101: 2}
    assert result.damaged_stock_additions == {101: 1}
    assert result.remaining_under_inspection == {702: 2}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("return_item_id", 0),
        ("return_item_id", True),
        ("return_item_id", "701"),
        ("quantity", 0),
        ("quantity", -1),
        ("quantity", True),
        ("condition", "UNKNOWN"),
    ],
)
def test_invalid_inspection_item_values_are_rejected(field, value):
    item = {"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}
    item[field] = value
    with pytest.raises(ValueError):
        validate_return_inspection(
            make_principal(), make_return(), RETURNED_ITEMS, 801, [item]
        )


@pytest.mark.parametrize("inspection_id", [0, -1, True, "801"])
def test_inspection_id_must_be_a_positive_integer(inspection_id):
    with pytest.raises(ValueError, match="inspection_id"):
        validate_return_inspection(
            make_principal(),
            make_return(),
            RETURNED_ITEMS,
            inspection_id,
            [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}],
        )


def test_unknown_or_mismatched_return_items_are_rejected():
    with pytest.raises(ValueError, match="not returned"):
        validate_return_inspection(
            make_principal(),
            make_return(),
            RETURNED_ITEMS,
            801,
            [{"return_item_id": 999, "quantity": 1, "condition": "RESALABLE"}],
        )
    with pytest.raises(ValueError, match="do not match"):
        validate_return_inspection(
            make_principal(),
            make_return(),
            [{"return_item_id": 701, "product_variant_id": 101, "quantity": 2}],
            801,
            [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}],
        )


def test_reprocessing_an_inspection_in_trusted_history_is_rejected():
    prior = ReturnInspection(
        801,
        (ReturnInspectionItem(701, 1, InspectionCondition.RESALABLE),),
    )
    with pytest.raises(ValueError, match="already been processed"):
        calculate_inspection_inventory_changes(
            make_principal(),
            make_return(),
            RETURNED_ITEMS,
            801,
            [{"return_item_id": 701, "quantity": 1, "condition": "RESALABLE"}],
            inspection_history=[prior],
        )


def test_history_cannot_exceed_returned_quantity_or_repeat_inspection_ids():
    too_much = ReturnInspection(
        801,
        (ReturnInspectionItem(701, 4, InspectionCondition.DAMAGED),),
    )
    with pytest.raises(ValueError, match="Cumulative inspected quantity"):
        calculate_remaining_under_inspection_quantities(
            make_return(), RETURNED_ITEMS, [too_much]
        )

    duplicate_history = [
        ReturnInspection(801, (ReturnInspectionItem(701, 1, InspectionCondition.DAMAGED),)),
        ReturnInspection(801, (ReturnInspectionItem(701, 1, InspectionCondition.RESALABLE),)),
    ]
    with pytest.raises(ValueError, match="duplicate inspection ID"):
        calculate_remaining_under_inspection_quantities(
            make_return(), RETURNED_ITEMS, duplicate_history
        )
