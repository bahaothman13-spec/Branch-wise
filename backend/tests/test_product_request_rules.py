"""Tests for pure product request rules; no database or product data is used."""

import pytest

from permissions import (
    AuthenticatedPrincipal,
    PRODUCT_CATALOG_APPROVAL,
    PRODUCT_REQUEST_COMMENT,
    PRODUCT_REQUEST_REVIEW,
    PRODUCT_REQUEST_SUBMIT,
    Role,
)
from product_request_rules import (
    ProductChangeType,
    ProductRequest,
    ProductRequestStatus,
    validate_request_approval,
    validate_request_comment,
    validate_request_rejection,
    validate_request_review,
    validate_request_submission,
)


def make_principal(role, business_id=10, branch_id=22):
    permissions = {
        Role.OWNER: {PRODUCT_CATALOG_APPROVAL},
        Role.BRANCH_MANAGER: {PRODUCT_REQUEST_REVIEW, PRODUCT_REQUEST_COMMENT},
        Role.EMPLOYEE: {PRODUCT_REQUEST_SUBMIT},
    }[role]
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=frozenset(permissions),
    )


def make_request(**changes):
    values = {
        "business_id": 10,
        "branch_id": 22,
        "change_type": ProductChangeType.ADD_PRODUCT,
    }
    values.update(changes)
    return ProductRequest(**values)


@pytest.mark.parametrize("change_type", list(ProductChangeType))
def test_employee_can_submit_product_and_variant_requests(change_type):
    request = validate_request_submission(make_principal(Role.EMPLOYEE), change_type)

    assert request.change_type is change_type
    assert request.status is ProductRequestStatus.PENDING
    assert request.business_id == 10
    assert request.branch_id == 22


def test_employee_can_submit_request_for_a_shared_catalog_change():
    request = validate_request_submission(
        make_principal(Role.EMPLOYEE),
        ProductChangeType.MODIFY_VARIANT,
        is_shared_catalog=True,
    )

    assert request.is_shared_catalog is True
    assert request.branch_id == 22


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_only_employee_can_submit_requests(role):
    with pytest.raises(PermissionError):
        validate_request_submission(
            make_principal(role), ProductChangeType.ADD_PRODUCT
        )


def test_invalid_change_type_is_rejected():
    with pytest.raises(ValueError, match="change type"):
        validate_request_submission(make_principal(Role.EMPLOYEE), "Delete Product")


def test_branch_manager_can_review_and_comment_on_same_branch_request():
    manager = make_principal(Role.BRANCH_MANAGER)
    request = make_request()

    validate_request_review(manager, request)
    validate_request_comment(manager, request, "Looks correct.")


def test_branch_manager_can_comment_on_shared_catalog_request_from_their_branch():
    manager = make_principal(Role.BRANCH_MANAGER)
    shared_request = make_request(is_shared_catalog=True)

    validate_request_review(manager, shared_request)
    validate_request_comment(manager, shared_request, "Please add the barcode.")


def test_manager_cannot_review_requests_outside_assigned_branch():
    with pytest.raises(PermissionError):
        validate_request_review(
            make_principal(Role.BRANCH_MANAGER), make_request(branch_id=23)
        )


def test_manager_cannot_comment_on_requests_outside_assigned_branch():
    with pytest.raises(PermissionError):
        validate_request_comment(
            make_principal(Role.BRANCH_MANAGER),
            make_request(branch_id=23),
            "Comment",
        )


def test_employee_cannot_review_or_comment_on_requests():
    employee = make_principal(Role.EMPLOYEE)
    request = make_request()

    with pytest.raises(PermissionError):
        validate_request_review(employee, request)
    with pytest.raises(PermissionError):
        validate_request_comment(employee, request, "Comment")


def test_empty_comment_is_rejected():
    with pytest.raises(ValueError, match="comment is required"):
        validate_request_comment(
            make_principal(Role.BRANCH_MANAGER), make_request(), "   "
        )


def test_same_business_owner_can_approve_a_request():
    validate_request_approval(make_principal(Role.OWNER), make_request())


def test_same_business_owner_can_reject_a_request_with_a_reason():
    validate_request_rejection(
        make_principal(Role.OWNER),
        make_request(is_shared_catalog=True),
        "Duplicate item.",
    )


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.BRANCH_MANAGER])
def test_employee_and_branch_manager_cannot_approve_requests(role):
    with pytest.raises(PermissionError):
        validate_request_approval(make_principal(role), make_request())


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.BRANCH_MANAGER])
def test_employee_and_branch_manager_cannot_reject_shared_catalog_requests(role):
    with pytest.raises(PermissionError):
        validate_request_rejection(
            make_principal(role),
            make_request(is_shared_catalog=True),
            "Not suitable.",
        )


def test_owner_from_another_business_cannot_approve():
    with pytest.raises(PermissionError):
        validate_request_approval(
            make_principal(Role.OWNER, business_id=99), make_request()
        )


def test_owner_from_another_business_cannot_reject():
    with pytest.raises(PermissionError):
        validate_request_rejection(
            make_principal(Role.OWNER, business_id=99), make_request(), "Reason"
        )


@pytest.mark.parametrize("reason", [None, "", "   ", 42])
def test_rejection_requires_a_non_empty_reason(reason):
    with pytest.raises(ValueError, match="rejection reason"):
        validate_request_rejection(make_principal(Role.OWNER), make_request(), reason)


@pytest.mark.parametrize(
    "decided_status",
    [ProductRequestStatus.APPROVED, ProductRequestStatus.REJECTED],
)
def test_decided_request_cannot_be_approved_or_rejected_again(decided_status):
    owner = make_principal(Role.OWNER)
    request = make_request(status=decided_status)

    with pytest.raises(ValueError, match="Only pending"):
        validate_request_approval(owner, request)
    with pytest.raises(ValueError, match="Only pending"):
        validate_request_rejection(owner, request, "Reason")


def test_validation_does_not_modify_requests_or_active_products():
    pending = make_request()
    rejected = make_request(status=ProductRequestStatus.REJECTED)
    original_pending = pending.__dict__.copy()
    original_rejected = rejected.__dict__.copy()
    active_products = [{"id": 5, "name": "Tea"}]
    original_products = [product.copy() for product in active_products]

    validate_request_approval(make_principal(Role.OWNER), pending)
    with pytest.raises(ValueError):
        validate_request_approval(make_principal(Role.OWNER), rejected)

    assert pending.__dict__ == original_pending
    assert rejected.__dict__ == original_rejected
    assert active_products == original_products
