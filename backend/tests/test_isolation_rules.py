"""Tests for pure business and branch isolation rules."""

from types import SimpleNamespace

import pytest

from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    can_access_resource,
    can_access_transfer,
    filter_accessible_resources,
    is_valid_trusted_context,
    require_resource_access,
    resources_belong_to_business,
)
from permissions import (
    INVENTORY_ADJUST,
    INVENTORY_VIEW,
    REPORTS_VIEW_BUSINESS,
    SALES_RECORD,
    SALES_VIEW,
    SALES_VIEW_AUTHORIZED,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_REQUEST,
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)


def make_principal(
    role=Role.OWNER,
    business_id=10,
    branch_id=None,
    permissions=None,
):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=branch_id,
        permissions=(
            ROLE_PERMISSIONS.get(role, frozenset())
            if permissions is None
            else permissions
        ),
    )


def scope(business_id=10, branch_id=22):
    return ResourceScope(business_id=business_id, branch_id=branch_id)


def test_owner_can_access_branch_and_business_resources_in_own_business():
    owner = make_principal()

    assert can_access_resource(owner, INVENTORY_VIEW, scope(branch_id=22))
    assert can_access_resource(
        owner,
        REPORTS_VIEW_BUSINESS,
        scope(branch_id=None),
    )


def test_owner_is_denied_resources_owned_by_another_business():
    owner = make_principal()

    assert not can_access_resource(owner, INVENTORY_VIEW, scope(business_id=99))


def test_branch_manager_can_access_resources_at_the_assigned_branch():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)

    assert can_access_resource(manager, INVENTORY_ADJUST, scope(branch_id=22))


def test_branch_manager_is_denied_another_branch_in_the_same_business():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)

    assert not can_access_resource(manager, INVENTORY_ADJUST, scope(branch_id=23))


def test_employee_is_limited_by_both_branch_and_role_permissions():
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions={SALES_RECORD, SALES_VIEW_AUTHORIZED, INVENTORY_ADJUST},
    )

    assert can_access_resource(employee, SALES_RECORD, scope(branch_id=22))
    assert not can_access_resource(employee, SALES_RECORD, scope(branch_id=23))
    assert not can_access_resource(employee, SALES_VIEW, scope(branch_id=22))
    assert not can_access_resource(
        employee,
        INVENTORY_ADJUST,
        scope(branch_id=22),
        # A client or custom grant cannot expand the Employee role matrix.
    )


def test_forged_business_and_branch_identifiers_do_not_grant_access():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)
    forged_owner = make_principal(
        "Owner",
        business_id=10,
        permissions={INVENTORY_ADJUST},
    )

    # A client-shaped mapping is not an accepted server-loaded scope.
    assert not can_access_resource(
        manager,
        INVENTORY_ADJUST,
        {"business_id": 10, "branch_id": 22},
    )
    # The principal's assigned branch and business remain authoritative.
    assert not can_access_resource(manager, INVENTORY_ADJUST, scope(branch_id=23))
    assert not can_access_resource(manager, INVENTORY_ADJUST, scope(business_id=99))
    assert not can_access_resource(forged_owner, INVENTORY_ADJUST, scope())


def test_referenced_products_and_variants_must_belong_to_the_same_business():
    owner = make_principal()
    inventory = scope(branch_id=22)
    product = scope(branch_id=None)
    variant = scope(branch_id=None)

    assert resources_belong_to_business(10, inventory, product, variant)
    assert can_access_resource(
        owner,
        INVENTORY_ADJUST,
        inventory,
        related_scopes=(product, variant),
    )
    assert not can_access_resource(
        owner,
        INVENTORY_ADJUST,
        inventory,
        related_scopes=(product, scope(business_id=99, branch_id=None)),
    )
    assert not resources_belong_to_business(10, inventory, scope(business_id=99))


def test_transfer_requires_both_branches_in_the_same_business():
    owner = make_principal()
    source = scope(branch_id=22)
    destination = scope(branch_id=23)

    assert can_access_transfer(owner, TRANSFERS_REQUEST, source, destination)
    assert not can_access_transfer(
        owner,
        TRANSFERS_REQUEST,
        source,
        scope(business_id=99, branch_id=23),
    )


def test_transfer_checks_permission_at_the_operation_branch():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)
    source = scope(branch_id=22)
    destination = scope(branch_id=23)
    destination_manager = make_principal(Role.BRANCH_MANAGER, branch_id=23)

    assert can_access_transfer(
        manager,
        TRANSFERS_REQUEST,
        source,
        destination,
        permission_branch="source",
    )
    assert not can_access_transfer(
        manager,
        TRANSFERS_CONFIRM_RECEIPT,
        source,
        destination,
        permission_branch="destination",
    )
    assert can_access_transfer(
        destination_manager,
        TRANSFERS_CONFIRM_RECEIPT,
        source,
        destination,
        permission_branch="destination",
    )
    assert not can_access_transfer(
        manager,
        TRANSFERS_REQUEST,
        source,
        destination,
        permission_branch="both",
    )


@pytest.mark.parametrize(
    ("principal", "resource"),
    [
        (None, scope()),
        (make_principal(business_id=True), scope()),
        (make_principal(Role.EMPLOYEE, branch_id=None), scope()),
        (make_principal(Role.EMPLOYEE, branch_id=True), scope()),
        (make_principal(), ResourceScope(business_id=True, branch_id=22)),
        (make_principal(), ResourceScope(business_id=10, branch_id=True)),
        (make_principal(), None),
    ],
)
def test_invalid_or_missing_trusted_context_fails_closed(principal, resource):
    assert not is_valid_trusted_context(principal, resource)
    assert not can_access_resource(principal, INVENTORY_VIEW, resource)


def test_collection_filter_omits_other_branches_businesses_and_invalid_rows():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)
    allowed = SimpleNamespace(id=1, scope=scope(branch_id=22))
    other_branch = SimpleNamespace(id=2, scope=scope(branch_id=23))
    other_business = SimpleNamespace(
        id=3,
        scope=scope(business_id=99, branch_id=22),
    )
    invalid = SimpleNamespace(id=4, scope={"business_id": 10, "branch_id": 22})

    visible = filter_accessible_resources(
        manager,
        INVENTORY_VIEW,
        [allowed, other_branch, other_business, invalid],
        scope_getter=lambda item: item.scope,
    )

    assert visible == (allowed,)
    assert all(item.id == 1 for item in visible)


def test_collection_filter_omits_resources_with_foreign_related_records():
    owner = make_principal()
    allowed = SimpleNamespace(id=1, scope=scope(branch_id=22), variant=scope(10, None))
    foreign_variant = SimpleNamespace(
        id=2,
        scope=scope(branch_id=22),
        variant=scope(99, None),
    )

    visible = filter_accessible_resources(
        owner,
        INVENTORY_VIEW,
        [allowed, foreign_variant],
        scope_getter=lambda item: item.scope,
        related_scopes_getter=lambda item: (item.variant,),
    )

    assert visible == (allowed,)


def test_denial_message_does_not_reveal_whether_a_resource_exists():
    manager = make_principal(Role.BRANCH_MANAGER, branch_id=22)
    foreign_branch = scope(branch_id=23)

    with pytest.raises(PermissionError) as error:
        require_resource_access(manager, INVENTORY_VIEW, foreign_branch)

    assert str(error.value) == ACCESS_DENIED_MESSAGE
    assert "23" not in str(error.value)


def test_missing_permission_grant_is_denied_even_when_role_allows_it():
    employee = make_principal(
        Role.EMPLOYEE,
        branch_id=22,
        permissions=frozenset(),
    )

    assert not can_access_resource(employee, SALES_VIEW_AUTHORIZED, scope())
