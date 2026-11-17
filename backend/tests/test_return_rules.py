"""Unit tests for pure return rules; no database or application session is used."""

from dataclasses import replace

import pytest

from permissions import (
    AuthenticatedPrincipal,
    RETURNS_INSPECT,
    RETURNS_RECORD,
    SALE_CANCELLATION_APPROVAL,
    SALE_CANCELLATION_REQUEST,
    Role,
)
from return_rules import (
    ReturnRequest,
    ReturnStatus,
    calculate_cumulative_return_quantities,
    sale_has_processed_returns,
    validate_return_inspection,
    validate_return_items,
    validate_return_submission,
    validate_unlinked_return_verification,
)
from sale_cancellation_rules import (
    Sale,
    SaleCancellationRequest,
    SaleStatus,
    validate_request_approval,
    validate_request_submission,
)


def make_principal(role, business_id=10, branch_id=22, permissions=None):
    if permissions is None:
        permissions = {
            Role.OWNER: {RETURNS_RECORD, RETURNS_INSPECT},
            Role.BRANCH_MANAGER: {RETURNS_RECORD, RETURNS_INSPECT},
            Role.EMPLOYEE: {RETURNS_RECORD},
        }[role]
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def make_sale(**changes):
    values = {
        "sale_id": 501,
        "business_id": 10,
        "branch_id": 22,
        "status": SaleStatus.COMPLETED,
        "items": [
            {"product_variant_id": 101, "quantity": 3},
            {"product_variant_id": 202, "quantity": 1},
        ],
        "has_processed_returns": False,
    }
    values.update(changes)
    return Sale(**values)


def make_return(**changes):
    values = {
        "business_id": 10,
        "branch_id": 22,
        "reason": "Customer changed their mind.",
        "items": [{"product_variant_id": 101, "quantity": 1}],
        "original_sale_id": 501,
        "status": ReturnStatus.PENDING,
    }
    values.update(changes)
    return ReturnRequest(**values)


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_authorized_roles_can_record_a_return_in_scope(role):
    returned = validate_return_submission(
        make_principal(role),
        [{"product_variant_id": 101, "quantity": 1}],
        "  Customer changed their mind.  ",
        sale=make_sale(),
    )

    assert returned == ReturnRequest(
        business_id=10,
        branch_id=22,
        reason="Customer changed their mind.",
        items=[{"product_variant_id": 101, "quantity": 1}],
        original_sale_id=501,
        status=ReturnStatus.PENDING,
    )


def test_unlinked_return_can_be_recorded_in_trusted_branch_scope():
    returned = validate_return_submission(
        make_principal(Role.EMPLOYEE),
        [{"product_variant_id": 404, "quantity": 2}],
        "Customer brought an item without a receipt.",
        branch_id=22,
    )

    assert returned.original_sale_id is None
    assert returned.status is ReturnStatus.PENDING
    assert returned.business_id == 10
    assert returned.branch_id == 22


def test_missing_return_permission_is_denied():
    with pytest.raises(PermissionError):
        validate_return_submission(
            make_principal(Role.EMPLOYEE, permissions=set()),
            [{"product_variant_id": 101, "quantity": 1}],
            "Reason",
            sale=make_sale(),
        )


def test_return_recording_is_denied_outside_business_or_branch_scope():
    with pytest.raises(PermissionError):
        validate_return_submission(
            make_principal(Role.OWNER, business_id=99),
            [{"product_variant_id": 101, "quantity": 1}],
            "Reason",
            sale=make_sale(),
        )

    with pytest.raises(PermissionError):
        validate_return_submission(
            make_principal(Role.BRANCH_MANAGER, branch_id=23),
            [{"product_variant_id": 101, "quantity": 1}],
            "Reason",
            sale=make_sale(),
        )

    with pytest.raises(PermissionError):
        validate_return_submission(
            make_principal(Role.EMPLOYEE, branch_id=23),
            [{"product_variant_id": 404, "quantity": 1}],
            "Reason",
            branch_id=22,
        )


@pytest.mark.parametrize(
    "item",
    [
        {"product_variant_id": 0, "quantity": 1},
        {"product_variant_id": -1, "quantity": 1},
        {"product_variant_id": True, "quantity": 1},
        {"product_variant_id": "101", "quantity": 1},
        {"product_variant_id": 101, "quantity": 0},
        {"product_variant_id": 101, "quantity": -1},
        {"product_variant_id": 101, "quantity": False},
        {"product_variant_id": 101},
        {"quantity": 1},
        None,
    ],
)
def test_invalid_return_item_or_quantity_is_rejected(item):
    with pytest.raises(ValueError):
        validate_return_items([item])


def test_empty_return_items_are_rejected():
    with pytest.raises(ValueError, match="at least one"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE), [], "Reason", sale=make_sale()
        )


@pytest.mark.parametrize("reason", [None, "", "   ", 7])
def test_return_reason_is_required(reason):
    with pytest.raises(ValueError, match="return reason"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE),
            [{"product_variant_id": 101, "quantity": 1}],
            reason,
            sale=make_sale(),
        )


def test_new_linked_returns_are_rejected_after_sale_cancellation():
    with pytest.raises(ValueError, match="cancelled sale"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE),
            [{"product_variant_id": 101, "quantity": 1}],
            "Reason",
            sale=make_sale(status=SaleStatus.CANCELLED),
        )

    with pytest.raises(ValueError, match="Unknown sale status"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE),
            [{"product_variant_id": 101, "quantity": 1}],
            "Reason",
            sale=make_sale(status="Pending"),
        )


def test_linked_return_variant_must_be_present_on_the_original_sale():
    with pytest.raises(ValueError, match="not sold"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE),
            [{"product_variant_id": 999, "quantity": 1}],
            "Reason",
            sale=make_sale(),
        )


@pytest.mark.parametrize(
    "previous_status",
    [
        ReturnStatus.PENDING,
        ReturnStatus.VERIFIED,
        ReturnStatus.PARTIALLY_INSPECTED,
        ReturnStatus.PROCESSED,
    ],
)
def test_cumulative_limit_includes_pending_and_partially_inspected_returns(
    previous_status,
):
    previous = make_return(
        items=[{"product_variant_id": 101, "quantity": 2}],
        status=previous_status,
    )

    with pytest.raises(ValueError, match="Cumulative return quantity"):
        validate_return_submission(
            make_principal(Role.EMPLOYEE),
            [{"product_variant_id": 101, "quantity": 2}],
            "Reason",
            sale=make_sale(),
            existing_returns=[previous],
        )


def test_cumulative_quantities_aggregate_lines_and_exact_remaining_amount_is_allowed():
    sale = make_sale(
        items=[
            {"product_variant_id": 101, "quantity": 4},
            {"product_variant_id": 101, "quantity": 1},
        ]
    )
    previous = make_return(
        items=[
            {"product_variant_id": 101, "quantity": 2},
            {"product_variant_id": 101, "quantity": 1},
        ],
        status=ReturnStatus.PARTIALLY_INSPECTED,
    )

    assert calculate_cumulative_return_quantities(sale, [previous]) == {101: 3}
    result = validate_return_submission(
        make_principal(Role.EMPLOYEE),
        [{"product_variant_id": 101, "quantity": 2}],
        "Remaining quantity.",
        sale=sale,
        existing_returns=[previous],
    )
    assert result.items == [{"product_variant_id": 101, "quantity": 2}]


def test_rejected_prior_return_does_not_consume_the_sold_quantity_limit():
    previous = make_return(
        items=[{"product_variant_id": 101, "quantity": 3}],
        status=ReturnStatus.REJECTED,
    )

    result = validate_return_submission(
        make_principal(Role.EMPLOYEE),
        [{"product_variant_id": 101, "quantity": 3}],
        "Retry after rejection.",
        sale=make_sale(),
        existing_returns=[previous],
    )

    assert result.items == [{"product_variant_id": 101, "quantity": 3}]


def test_existing_return_for_another_business_branch_cannot_be_counted_for_sale():
    foreign = make_return(business_id=99)
    with pytest.raises(ValueError, match="scope"):
        calculate_cumulative_return_quantities(make_sale(), [foreign])


def test_unlinked_return_must_be_verified_by_an_authorized_owner_or_manager():
    employee = make_principal(Role.EMPLOYEE)
    manager = make_principal(Role.BRANCH_MANAGER)
    pending = validate_return_submission(
        employee,
        [{"product_variant_id": 404, "quantity": 2}],
        "No receipt was provided.",
        branch_id=22,
    )

    with pytest.raises(ValueError, match="verified before inspection"):
        validate_return_inspection(
            manager, pending, [{"product_variant_id": 404, "quantity": 1}]
        )
    with pytest.raises(PermissionError):
        validate_unlinked_return_verification(employee, pending)

    verified = validate_unlinked_return_verification(manager, pending)
    assert verified.status is ReturnStatus.VERIFIED
    assert pending.status is ReturnStatus.PENDING


def test_unlinked_return_verification_obeys_business_branch_and_grant_scope():
    pending = ReturnRequest(
        business_id=10,
        branch_id=22,
        reason="No receipt was provided.",
        items=[{"product_variant_id": 404, "quantity": 1}],
    )

    with pytest.raises(PermissionError):
        validate_unlinked_return_verification(
            make_principal(Role.BRANCH_MANAGER, branch_id=23), pending
        )
    with pytest.raises(PermissionError):
        validate_unlinked_return_verification(
            make_principal(Role.OWNER, business_id=99), pending
        )
    with pytest.raises(PermissionError):
        validate_unlinked_return_verification(
            make_principal(Role.OWNER, permissions={RETURNS_RECORD}), pending
        )


def test_inspection_plan_is_authorized_and_does_not_exceed_returned_quantities():
    manager = make_principal(Role.BRANCH_MANAGER)
    request = make_return(
        items=[{"product_variant_id": 101, "quantity": 3}],
        status=ReturnStatus.PARTIALLY_INSPECTED,
    )
    original_request = request.__dict__.copy()
    accepted = validate_return_inspection(
        manager,
        request,
        [{"product_variant_id": 101, "quantity": 1}],
        previously_inspected_items=[{"product_variant_id": 101, "quantity": 1}],
    )

    assert accepted == {101: 1}
    assert request.__dict__ == original_request
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_return_inspection(
            manager,
            request,
            [{"product_variant_id": 101, "quantity": 3}],
            previously_inspected_items=[{"product_variant_id": 101, "quantity": 1}],
        )


def test_employee_cannot_accept_return_quantities_into_inspection_inventory():
    request = make_return()
    with pytest.raises(PermissionError):
        validate_return_inspection(
            make_principal(Role.EMPLOYEE),
            request,
            [{"product_variant_id": 101, "quantity": 1}],
        )


def test_processed_or_unrequested_variants_cannot_be_inspected_again():
    manager = make_principal(Role.BRANCH_MANAGER)
    with pytest.raises(ValueError, match="cannot be inspected again"):
        validate_return_inspection(
            manager,
            make_return(status=ReturnStatus.PROCESSED),
            [{"product_variant_id": 101, "quantity": 1}],
        )
    with pytest.raises(ValueError, match="not returned"):
        validate_return_inspection(
            manager,
            make_return(),
            [{"product_variant_id": 999, "quantity": 1}],
        )


def test_inspected_returns_do_not_block_cancellation_request_or_approval():
    sale = make_sale()
    employee = make_principal(
        Role.EMPLOYEE,
        permissions={RETURNS_RECORD, SALE_CANCELLATION_REQUEST},
    )
    owner = make_principal(Role.OWNER, permissions={SALE_CANCELLATION_APPROVAL})
    for status in (ReturnStatus.PARTIALLY_INSPECTED, ReturnStatus.PROCESSED):
        existing = make_return(status=status)
        assert sale_has_processed_returns(sale, [existing])
        request = validate_request_submission(
            employee,
            replace(sale, has_processed_returns=True),
            "Cancel sale",
            existing_returns=[existing],
        )
        validate_request_approval(
            owner,
            request,
            replace(sale, has_processed_returns=True),
            existing_returns=[existing],
        )


def test_pending_return_does_not_count_as_a_processed_return_for_cancellation():
    sale = make_sale()
    assert not sale_has_processed_returns(sale, [make_return()])
    request = validate_request_submission(
        make_principal(
            Role.EMPLOYEE,
            permissions={RETURNS_RECORD, SALE_CANCELLATION_REQUEST},
        ),
        replace(sale, has_processed_returns=False),
        "Cancel sale",
    )
    assert isinstance(request, SaleCancellationRequest)
