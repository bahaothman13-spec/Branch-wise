"""Tests for the approved BranchWise role matrix and resource scope rules."""

import pytest

from permissions import (
    AuthenticatedPrincipal,
    ROLE_PERMISSIONS,
    ResourceScope,
    Role,
    has_permission,
)


EXPECTED_ROLE_PERMISSIONS = {
    Role.OWNER: {
        "dashboard:view",
        "products:view",
        "product_catalog:manage",
        "product_catalog:approve",
        "inventory:view",
        "inventory:adjust",
        "sales:record",
        "sales:view",
        "sale_cancellation:approve",
        "users:manage",
        "branches:manage",
        "business_settings:manage",
        "suppliers:view",
        "suppliers:manage",
        "purchases:manage",
        "transfers:request",
        "transfers:approve_source",
        "transfers:dispatch",
        "transfers:confirm_receipt",
        "returns:record",
        "returns:inspect",
        "reports:view_business",
        "ai:recommendations",
        "ai:assistant",
        "ai:decision_log",
        "password:change_self",
    },
    Role.BRANCH_MANAGER: {
        "dashboard:view",
        "products:view",
        "product_request:review",
        "product_request:comment",
        "inventory:view",
        "inventory:adjust",
        "sales:record",
        "sales:view",
        "sale_cancellation:approve",
        "suppliers:view",
        "purchases:manage_authorized",
        "transfers:request",
        "transfers:approve_source",
        "transfers:dispatch",
        "transfers:confirm_receipt",
        "returns:record",
        "returns:inspect",
        "reports:view_branch",
        "ai:recommendations",
        "ai:assistant",
        "ai:decision_log",
        "password:change_self",
    },
    Role.EMPLOYEE: {
        "dashboard:view",
        "products:view",
        "inventory:view",
        "sales:record",
        "sales:view_authorized",
        "product_request:submit",
        "sale_cancellation:request",
        "returns:record",
        "password:change_self",
    },
}


def make_principal(role, business_id=10, branch_id=None, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=frozenset(permissions or ()),
    )


def test_role_permissions_match_the_approved_matrix():
    assert dict(ROLE_PERMISSIONS) == {
        role: frozenset(permissions)
        for role, permissions in EXPECTED_ROLE_PERMISSIONS.items()
    }


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (role, permission)
        for role, permissions in EXPECTED_ROLE_PERMISSIONS.items()
        for permission in sorted(permissions)
    ],
)
def test_every_approved_permission_is_granted_with_trusted_grant_and_scope(
    role, permission
):
    branch_id = None if role is Role.OWNER else 22
    principal = make_principal(
        role,
        branch_id=branch_id,
        permissions=EXPECTED_ROLE_PERMISSIONS[role],
    )
    resource = ResourceScope(business_id=10, branch_id=23 if role is Role.OWNER else 22)

    assert has_permission(principal, permission, resource)


@pytest.mark.parametrize(
    ("role", "permission"),
    [
        (role, permission)
        for role, permissions in EXPECTED_ROLE_PERMISSIONS.items()
        for permission in sorted(
            set.union(*EXPECTED_ROLE_PERMISSIONS.values()) - permissions
        )
    ],
)
def test_role_cannot_use_permission_outside_its_matrix_even_if_granted(
    role, permission
):
    principal = make_principal(
        role,
        branch_id=None if role is Role.OWNER else 22,
        permissions={permission},
    )
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(principal, permission, resource)


@pytest.mark.parametrize("role", list(Role))
def test_permission_also_requires_a_trusted_server_side_grant(role):
    branch_id = None if role is Role.OWNER else 22
    permission = next(iter(EXPECTED_ROLE_PERMISSIONS[role]))
    principal = make_principal(role, branch_id=branch_id)
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(principal, permission, resource)


@pytest.mark.parametrize("role", list(Role))
def test_all_roles_are_denied_access_to_another_business(role):
    branch_id = None if role is Role.OWNER else 22
    permission = next(iter(EXPECTED_ROLE_PERMISSIONS[role]))
    principal = make_principal(
        role,
        branch_id=branch_id,
        permissions={permission},
    )
    resource = ResourceScope(business_id=99, branch_id=22)

    assert not has_permission(principal, permission, resource)


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_branch_scoped_roles_cannot_access_another_branch(role):
    permission = next(iter(EXPECTED_ROLE_PERMISSIONS[role]))
    principal = make_principal(role, branch_id=22, permissions={permission})
    resource = ResourceScope(business_id=10, branch_id=23)

    assert not has_permission(principal, permission, resource)


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_branch_scoped_roles_need_an_assigned_and_resource_branch(role):
    permission = next(iter(EXPECTED_ROLE_PERMISSIONS[role]))
    principal_without_branch = make_principal(role, permissions={permission})
    resource_without_branch = ResourceScope(business_id=10, branch_id=None)
    principal = make_principal(role, branch_id=22, permissions={permission})
    resource = ResourceScope(business_id=10, branch_id=None)

    assert not has_permission(
        principal_without_branch,
        permission,
        ResourceScope(business_id=10, branch_id=22),
    )
    assert not has_permission(principal, permission, resource_without_branch)
    assert not has_permission(principal, permission, resource)


def test_owner_can_access_branchless_and_other_branch_resources_in_own_business():
    owner = make_principal(
        Role.OWNER,
        permissions={"business_settings:manage", "inventory:view"},
    )

    assert has_permission(
        owner,
        "business_settings:manage",
        ResourceScope(business_id=10, branch_id=None),
    )
    assert has_permission(
        owner,
        "inventory:view",
        ResourceScope(business_id=10, branch_id=22),
    )


@pytest.mark.parametrize("missing_business_id", [True, False])
def test_missing_business_scope_fails_closed(missing_business_id):
    owner = make_principal(
        Role.OWNER,
        business_id=None if missing_business_id else 10,
        permissions={"dashboard:view"},
    )
    resource = ResourceScope(
        business_id=None if not missing_business_id else 10,
        branch_id=22,
    )

    assert not has_permission(owner, "dashboard:view", resource)


def test_client_like_role_string_is_not_accepted_as_trusted_role():
    principal = make_principal(
        "Owner",
        permissions=EXPECTED_ROLE_PERMISSIONS[Role.OWNER],
    )
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(principal, "branches:manage", resource)


def test_unknown_permission_is_denied():
    owner = make_principal(
        Role.OWNER,
        permissions={"unknown:operation"},
    )
    resource = ResourceScope(business_id=10, branch_id=22)

    assert not has_permission(owner, "unknown:operation", resource)
