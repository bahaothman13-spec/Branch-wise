"""Unit tests for pure sale cancellation rules; no database is used."""

import pytest

from permissions import (
    AuthenticatedPrincipal,
    SALE_CANCELLATION_APPROVAL,
    SALE_CANCELLATION_REQUEST,
    Role,
)
from sale_cancellation_rules import (
    ALLOWED_STATUS_TRANSITIONS,
    Sale,
    SaleCancellationRequest,
    SaleCancellationStatus,
    SaleStatus,
    calculate_restoration_quantities,
    validate_request_approval,
    validate_request_rejection,
    validate_request_submission,
    validate_sale_status_transition,
    validate_status_transition,
)


def make_principal(role, business_id=10, branch_id=22, permissions=None):
    if permissions is None:
        permissions = {
            Role.OWNER: {SALE_CANCELLATION_APPROVAL},
            Role.BRANCH_MANAGER: {SALE_CANCELLATION_APPROVAL},
            Role.EMPLOYEE: {SALE_CANCELLATION_REQUEST},
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
            {"product_variant_id": 101, "quantity": 2},
            {"product_variant_id": 202, "quantity": 1},
        ],
        "has_processed_returns": False,
    }
    values.update(changes)
    return Sale(**values)


def make_request(**changes):
    values = {
        "sale_id": 501,
        "business_id": 10,
        "branch_id": 22,
        "reason": "Incorrect item was sold.",
        "status": SaleCancellationStatus.PENDING,
    }
    values.update(changes)
    return SaleCancellationRequest(**values)


def test_employee_can_submit_request_for_completed_sale_in_assigned_branch():
    request = validate_request_submission(
        make_principal(Role.EMPLOYEE), make_sale(), "  Incorrect item.  "
    )

    assert request == SaleCancellationRequest(
        sale_id=501,
        business_id=10,
        branch_id=22,
        reason="Incorrect item.",
        status=SaleCancellationStatus.PENDING,
    )


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_only_employee_can_submit_cancellation_requests(role):
    with pytest.raises(PermissionError):
        validate_request_submission(make_principal(role), make_sale(), "Reason")


def test_employee_cannot_request_cancellation_for_another_branch():
    with pytest.raises(PermissionError):
        validate_request_submission(
            make_principal(Role.EMPLOYEE), make_sale(branch_id=23), "Reason"
        )


@pytest.mark.parametrize("reason", [None, "", "   ", 42])
def test_cancellation_reason_is_required(reason):
    with pytest.raises(ValueError, match="cancellation reason"):
        validate_request_submission(make_principal(Role.EMPLOYEE), make_sale(), reason)


def test_only_completed_sale_can_be_submitted_for_cancellation():
    with pytest.raises(ValueError, match="Only completed"):
        validate_request_submission(
            make_principal(Role.EMPLOYEE),
            make_sale(status=SaleStatus.CANCELLED),
            "Reason",
        )


def test_sale_with_processed_returns_cannot_be_requested_for_cancellation():
    with pytest.raises(ValueError, match="processed returns"):
        validate_request_submission(
            make_principal(Role.EMPLOYEE),
            make_sale(has_processed_returns=True),
            "Reason",
        )


def test_duplicate_pending_request_for_same_sale_is_rejected():
    existing = make_request()

    with pytest.raises(ValueError, match="already exists"):
        validate_request_submission(
            make_principal(Role.EMPLOYEE),
            make_sale(),
            "Another reason",
            existing_requests=[existing],
        )


def test_rejected_request_does_not_block_a_new_pending_request():
    rejected = make_request(status=SaleCancellationStatus.REJECTED)

    request = validate_request_submission(
        make_principal(Role.EMPLOYEE),
        make_sale(),
        "Updated reason",
        existing_requests=[rejected],
    )

    assert request.status is SaleCancellationStatus.PENDING


def test_owner_can_approve_request_in_their_business():
    validate_request_approval(make_principal(Role.OWNER), make_request(), make_sale())


def test_branch_manager_can_approve_request_in_their_branch():
    validate_request_approval(
        make_principal(Role.BRANCH_MANAGER), make_request(), make_sale()
    )


@pytest.mark.parametrize("role", [Role.EMPLOYEE])
def test_employee_cannot_approve_or_reject_cancellation_request(role):
    principal = make_principal(role)
    with pytest.raises(PermissionError):
        validate_request_approval(principal, make_request(), make_sale())
    with pytest.raises(PermissionError):
        validate_request_rejection(principal, make_request(), make_sale(), "Reason")


def test_manager_cannot_approve_request_outside_assigned_branch():
    with pytest.raises(PermissionError):
        validate_request_approval(
            make_principal(Role.BRANCH_MANAGER),
            make_request(branch_id=23),
            make_sale(branch_id=23),
        )


def test_owner_from_another_business_cannot_approve_or_reject():
    owner = make_principal(Role.OWNER, business_id=99)
    request = make_request()
    sale = make_sale()

    with pytest.raises(PermissionError):
        validate_request_approval(owner, request, sale)
    with pytest.raises(PermissionError):
        validate_request_rejection(owner, request, sale, "Reason")


def test_request_must_match_the_server_loaded_sale():
    with pytest.raises(ValueError, match="does not match"):
        validate_request_approval(
            make_principal(Role.OWNER), make_request(sale_id=502), make_sale()
        )


def test_approval_is_blocked_if_sale_has_processed_returns():
    with pytest.raises(ValueError, match="processed returns"):
        validate_request_approval(
            make_principal(Role.OWNER),
            make_request(),
            make_sale(has_processed_returns=True),
        )


def test_approval_is_blocked_if_sale_is_no_longer_completed():
    with pytest.raises(ValueError, match="Only completed"):
        validate_request_approval(
            make_principal(Role.OWNER),
            make_request(),
            make_sale(status=SaleStatus.CANCELLED),
        )


def test_rejection_requires_a_reason():
    with pytest.raises(ValueError, match="rejection reason"):
        validate_request_rejection(
            make_principal(Role.OWNER), make_request(), make_sale(), "   "
        )


def test_owner_and_branch_manager_can_reject_with_a_reason_in_scope():
    validate_request_rejection(
        make_principal(Role.OWNER), make_request(), make_sale(), "Duplicate sale."
    )
    validate_request_rejection(
        make_principal(Role.BRANCH_MANAGER),
        make_request(),
        make_sale(),
        "Duplicate sale.",
    )


@pytest.mark.parametrize("status", [SaleCancellationStatus.APPROVED, SaleCancellationStatus.REJECTED])
def test_decided_request_cannot_be_approved_or_rejected_again(status):
    principal = make_principal(Role.OWNER)
    request = make_request(status=status)
    sale = make_sale()

    with pytest.raises(ValueError, match="Only pending"):
        validate_request_approval(principal, request, sale)
    with pytest.raises(ValueError, match="Only pending"):
        validate_request_rejection(principal, request, sale, "Reason")


def test_restoration_calculation_aggregates_lines_for_an_approved_request():
    sale = make_sale(
        items=[
            {"product_variant_id": 101, "quantity": 2},
            {"product_variant_id": 101, "quantity": 3},
            {"product_variant_id": 202, "quantity": 1},
        ]
    )

    quantities = calculate_restoration_quantities(
        sale, make_request(status=SaleCancellationStatus.APPROVED)
    )

    assert quantities == {101: 5, 202: 1}


@pytest.mark.parametrize(
    "status", [SaleCancellationStatus.PENDING, SaleCancellationStatus.REJECTED]
)
def test_pending_and_rejected_requests_cannot_restore_inventory(status):
    sale = make_sale()
    request = make_request(status=status)
    original_sale = sale.__dict__.copy()
    original_request = request.__dict__.copy()
    inventory = {101: 8, 202: 4}
    original_inventory = inventory.copy()

    with pytest.raises(ValueError, match="Only approved"):
        calculate_restoration_quantities(sale, request)

    assert sale.__dict__ == original_sale
    assert request.__dict__ == original_request
    assert inventory == original_inventory


def test_cancelled_sale_cannot_be_planned_for_restoration_again():
    with pytest.raises(ValueError, match="Cannot change sale status"):
        calculate_restoration_quantities(
            make_sale(status=SaleStatus.CANCELLED),
            make_request(status=SaleCancellationStatus.APPROVED),
        )


def test_request_and_sale_status_transitions_are_explicit():
    assert ALLOWED_STATUS_TRANSITIONS[SaleCancellationStatus.PENDING] == {
        SaleCancellationStatus.APPROVED,
        SaleCancellationStatus.REJECTED,
    }
    validate_status_transition(
        SaleCancellationStatus.PENDING, SaleCancellationStatus.APPROVED
    )
    validate_status_transition(
        SaleCancellationStatus.PENDING, SaleCancellationStatus.REJECTED
    )
    validate_sale_status_transition(SaleStatus.COMPLETED, SaleStatus.CANCELLED)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (SaleCancellationStatus.APPROVED, SaleCancellationStatus.APPROVED),
        (SaleCancellationStatus.REJECTED, SaleCancellationStatus.APPROVED),
        (SaleCancellationStatus.PENDING, SaleCancellationStatus.PENDING),
        ("Unknown", SaleCancellationStatus.APPROVED),
    ],
)
def test_invalid_request_status_transitions_are_rejected(current, new):
    with pytest.raises(ValueError):
        validate_status_transition(current, new)


def test_sale_can_only_transition_from_completed_to_cancelled():
    with pytest.raises(ValueError, match="Cannot change sale status"):
        validate_sale_status_transition(SaleStatus.CANCELLED, SaleStatus.CANCELLED)


@pytest.mark.parametrize(
    "item",
    [
        {"product_variant_id": 0, "quantity": 1},
        {"product_variant_id": 101, "quantity": True},
        {"product_variant_id": "101", "quantity": 1},
        {"product_variant_id": 101},
        None,
    ],
)
def test_invalid_sale_items_are_rejected(item):
    with pytest.raises(ValueError):
        validate_request_submission(
            make_principal(Role.EMPLOYEE), make_sale(items=[item]), "Reason"
        )


def test_missing_request_permission_is_denied_even_for_employee_role():
    employee = make_principal(Role.EMPLOYEE, permissions=set())

    with pytest.raises(PermissionError):
        validate_request_submission(employee, make_sale(), "Reason")
