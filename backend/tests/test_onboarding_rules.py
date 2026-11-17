"""Unit tests for first-branch onboarding rules; no database is used."""

import pytest

from onboarding_rules import (
    AUTHENTICATION,
    BRANCH_DEPENDENT_PERMISSIONS,
    LOGOUT,
    ONBOARDING_REQUIRED_MESSAGE,
    can_create_first_branch,
    can_manage_branches,
    get_onboarding_status,
    is_branch_operation_ready,
    is_operation_allowed_during_onboarding,
)
from permissions import (
    BRANCHES_MANAGE,
    BUSINESS_SETTINGS_MANAGE,
    INVENTORY_ADJUST,
    PASSWORD_CHANGE_SELF,
    ROLE_PERMISSIONS,
    SALES_RECORD,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)


def make_principal(role=Role.OWNER, business_id=10, branch_id=None, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=(
            ROLE_PERMISSIONS.get(role, frozenset()) if permissions is None else permissions
        ),
    )


def test_owner_with_zero_branches_gets_first_branch_onboarding_result():
    status = get_onboarding_status(make_principal(), branch_count=0)

    assert status.required is True
    assert status.message == ONBOARDING_REQUIRED_MESSAGE
    assert "first branch" in status.message


def test_owner_with_an_existing_branch_has_completed_onboarding():
    status = get_onboarding_status(make_principal(), branch_count=1)

    assert status.required is False
    assert status.message is None


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_non_owner_does_not_receive_owner_first_branch_prompt(role):
    principal = make_principal(role, branch_id=22)

    assert get_onboarding_status(principal, branch_count=0).required is False


def test_owner_can_create_first_branch_only_in_their_own_empty_business():
    owner = make_principal()

    assert can_create_first_branch(owner, business_id=10, branch_count=0)
    assert not can_create_first_branch(owner, business_id=10, branch_count=1)
    assert not can_create_first_branch(owner, business_id=99, branch_count=0)


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_only_owner_can_manage_or_create_branches(role):
    principal = make_principal(role, branch_id=22)

    assert not can_manage_branches(principal, business_id=10)
    assert not can_create_first_branch(principal, business_id=10, branch_count=0)


@pytest.mark.parametrize("permission", sorted(BRANCH_DEPENDENT_PERMISSIONS))
def test_branch_dependent_operations_are_blocked_when_business_has_no_branches(
    permission,
):
    owner = make_principal()
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not is_branch_operation_ready(owner, permission, resource, branch_count=0)


@pytest.mark.parametrize("permission", [SALES_RECORD, INVENTORY_ADJUST])
def test_branch_dependent_operation_is_ready_with_branch_and_permission(permission):
    owner = make_principal()
    resource = ResourceScope(business_id=10, branch_id=22)

    assert is_branch_operation_ready(owner, permission, resource, branch_count=1)


def test_branch_dependent_operation_still_requires_role_permission_and_scope():
    employee = make_principal(Role.EMPLOYEE, branch_id=22)
    other_branch = ResourceScope(business_id=10, branch_id=23)
    other_business = ResourceScope(business_id=99, branch_id=22)
    owner_without_sales_grant = make_principal(permissions={BRANCHES_MANAGE})

    assert not is_branch_operation_ready(
        employee, SALES_RECORD, other_branch, branch_count=1
    )
    assert not is_branch_operation_ready(
        employee,
        SALES_RECORD,
        other_business,
        branch_count=1,
    )
    assert not is_branch_operation_ready(
        owner_without_sales_grant,
        SALES_RECORD,
        ResourceScope(business_id=10, branch_id=22),
        branch_count=1,
    )


def test_authentication_logout_and_permitted_setup_operations_remain_available():
    owner = make_principal()
    branchless_scope = ResourceScope(business_id=10, branch_id=None)
    owner_password_scope = ResourceScope(business_id=10, branch_id=None)

    assert is_operation_allowed_during_onboarding(None, AUTHENTICATION)
    assert is_operation_allowed_during_onboarding(None, LOGOUT)
    assert is_operation_allowed_during_onboarding(
        owner, BRANCHES_MANAGE, branchless_scope
    )
    assert is_operation_allowed_during_onboarding(
        owner, BUSINESS_SETTINGS_MANAGE, branchless_scope
    )
    assert is_operation_allowed_during_onboarding(
        owner, PASSWORD_CHANGE_SELF, owner_password_scope
    )
    assert not is_operation_allowed_during_onboarding(
        owner, SALES_RECORD, ResourceScope(business_id=10, branch_id=22)
    )


def test_employee_can_change_own_password_but_cannot_start_branch_setup():
    employee = make_principal(Role.EMPLOYEE, branch_id=22)

    assert is_operation_allowed_during_onboarding(
        employee,
        PASSWORD_CHANGE_SELF,
        ResourceScope(business_id=10, branch_id=22),
    )
    assert not is_operation_allowed_during_onboarding(
        employee,
        BRANCHES_MANAGE,
        ResourceScope(business_id=10, branch_id=None),
    )


@pytest.mark.parametrize("branch_count", [True, -1, 1.5, "0", None])
def test_invalid_branch_counts_are_rejected(branch_count):
    owner = make_principal()

    with pytest.raises(ValueError, match="branch_count"):
        get_onboarding_status(owner, branch_count)
    with pytest.raises(ValueError, match="branch_count"):
        can_create_first_branch(owner, business_id=10, branch_count=branch_count)
    with pytest.raises(ValueError, match="branch_count"):
        is_branch_operation_ready(
            owner,
            SALES_RECORD,
            ResourceScope(business_id=10, branch_id=22),
            branch_count,
        )


def test_untrusted_or_invalid_context_fails_closed():
    owner = make_principal()

    with pytest.raises(ValueError, match="trusted authenticated principal"):
        get_onboarding_status(None, branch_count=0)
    assert not can_manage_branches(owner, business_id=True)
    assert not is_branch_operation_ready(
        owner, SALES_RECORD, {"business_id": 10, "branch_id": 22}, branch_count=1
    )
    assert not is_branch_operation_ready(
        owner,
        SALES_RECORD,
        ResourceScope(business_id=10, branch_id=True),
        branch_count=1,
    )
    assert not is_operation_allowed_during_onboarding(
        owner, [BRANCHES_MANAGE], ResourceScope(business_id=10, branch_id=None)
    )


def test_client_like_role_and_foreign_business_scope_are_not_authorized():
    client_role = make_principal("Owner", business_id=10, permissions={BRANCHES_MANAGE})
    owner = make_principal()

    assert not can_manage_branches(client_role, business_id=10)
    assert not is_operation_allowed_during_onboarding(
        owner,
        BUSINESS_SETTINGS_MANAGE,
        ResourceScope(business_id=99, branch_id=None),
    )
