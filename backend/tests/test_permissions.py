"""Unit tests for BranchWise's isolated role and scope checks."""

import pytest

from permissions import (
    AuthenticatedPrincipal,
    PRODUCT_CATALOG_APPROVAL,
    ResourceScope,
    Role,
    SALE_CANCELLATION_APPROVAL,
    has_permission,
)


PERMISSION_A = "sample:operation-a"
PERMISSION_B = "sample:operation-b"


def make_principal(role, business_id=10, branch_id=None, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=frozenset(permissions or ()),
    )


def test_owner_can_access_granted_resource_in_another_branch_of_own_business():
    owner = make_principal(Role.OWNER, permissions={PERMISSION_A})
    resource = ResourceScope(business_id=10, branch_id=22)

    assert has_permission(owner, PERMISSION_A, resource)


def test_owner_cannot_access_resource_in_another_business():
    owner = make_principal(Role.OWNER, permissions={PERMISSION_A})
    other_business_resource = ResourceScope(business_id=99, branch_id=22)

    assert not has_permission(owner, PERMISSION_A, other_business_resource)


def test_branch_manager_can_access_granted_resource_in_assigned_branch():
    manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=22,
        permissions={PERMISSION_A},
    )
    resource = ResourceScope(business_id=10, branch_id=22)

    assert has_permission(manager, PERMISSION_A, resource)


def test_branch_manager_cannot_access_another_branch():
    manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=22,
        permissions={PERMISSION_A},
    )
    other_branch_resource = ResourceScope(business_id=10, branch_id=23)

    assert not has_permission(manager, PERMISSION_A, other_branch_resource)


def test_branch_manager_cannot_access_another_business_even_if_branch_id_matches():
    manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=22,
        permissions={PERMISSION_A},
    )
    other_business_resource = ResourceScope(business_id=99, branch_id=22)

    assert not has_permission(manager, PERMISSION_A, other_business_resource)


def test_employee_can_access_only_granted_operation_in_assigned_branch():
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions={PERMISSION_A},
    )
    resource = ResourceScope(business_id=10, branch_id=22)

    assert has_permission(employee, PERMISSION_A, resource)
    assert not has_permission(employee, PERMISSION_B, resource)


def test_employee_cannot_access_another_branch():
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions={PERMISSION_A},
    )
    other_branch_resource = ResourceScope(business_id=10, branch_id=23)

    assert not has_permission(employee, PERMISSION_A, other_branch_resource)


def test_user_without_an_assigned_branch_cannot_access_branch_resource():
    employee = make_principal(Role.EMPLOYEE, permissions={PERMISSION_A})
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(employee, PERMISSION_A, resource)


def test_any_role_without_an_explicit_grant_is_denied():
    owner = make_principal(Role.OWNER)
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(owner, PERMISSION_A, resource)


@pytest.mark.parametrize(
    "role, branch_id",
    [
        (Role.OWNER, None),
        (Role.BRANCH_MANAGER, 22),
        (Role.EMPLOYEE, 22),
    ],
)
def test_each_role_is_denied_when_permission_is_missing(role, branch_id):
    principal = make_principal(role, branch_id=branch_id)
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(principal, PERMISSION_A, resource)


def test_product_catalog_approval_is_owner_only():
    owner = make_principal(Role.OWNER, permissions={PRODUCT_CATALOG_APPROVAL})
    resource = ResourceScope(business_id=10, branch_id=22)
    manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=22,
        permissions={PRODUCT_CATALOG_APPROVAL},
    )
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions={PRODUCT_CATALOG_APPROVAL},
    )

    assert has_permission(owner, PRODUCT_CATALOG_APPROVAL, resource)
    assert not has_permission(manager, PRODUCT_CATALOG_APPROVAL, resource)
    assert not has_permission(employee, PRODUCT_CATALOG_APPROVAL, resource)


def test_sale_cancellation_approval_allows_owner_and_authorized_manager():
    resource = ResourceScope(business_id=10, branch_id=22)
    owner = make_principal(
        Role.OWNER,
        permissions={SALE_CANCELLATION_APPROVAL},
    )
    manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=22,
        permissions={SALE_CANCELLATION_APPROVAL},
    )

    assert has_permission(owner, SALE_CANCELLATION_APPROVAL, resource)
    assert has_permission(manager, SALE_CANCELLATION_APPROVAL, resource)


def test_sale_cancellation_approval_requires_grant_and_assigned_branch():
    resource = ResourceScope(business_id=10, branch_id=22)
    unauthorized_manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)
    other_branch_manager = make_principal(
        Role.BRANCH_MANAGER,
        branch_id=23,
        permissions={SALE_CANCELLATION_APPROVAL},
    )
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions={SALE_CANCELLATION_APPROVAL},
    )

    assert not has_permission(
        unauthorized_manager, SALE_CANCELLATION_APPROVAL, resource
    )
    assert not has_permission(
        other_branch_manager, SALE_CANCELLATION_APPROVAL, resource
    )
    assert not has_permission(employee, SALE_CANCELLATION_APPROVAL, resource)
