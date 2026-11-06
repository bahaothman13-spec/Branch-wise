"""Tests for pure shared category rules; no database is used."""

import pytest

from permissions import (
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from category_rules import (
    CATEGORY_NAME_MAX_LENGTH,
    CategoryContext,
    CategoryData,
    CategoryStatusProposal,
    ensure_category_name_available,
    validate_category_activation,
    validate_category_creation,
    validate_category_deactivation,
    validate_category_modification,
    validate_category_status_change,
)


def make_principal(role=Role.OWNER, business_id=10, branch_id=22, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=(
            ROLE_PERMISSIONS[role] if permissions is None else frozenset(permissions)
        ),
    )


def category(category_id=7, business_id=10, name="Beverages", is_active=True):
    return CategoryContext(
        category_id=category_id,
        name=name,
        scope=ResourceScope(business_id=business_id, branch_id=None),
        is_active=is_active,
    )


def test_owner_can_propose_category_creation_with_trimmed_name():
    proposal = validate_category_creation(make_principal(), name="  Beverages  ")

    assert proposal.data == CategoryData(name="Beverages")


@pytest.mark.parametrize(
    "name",
    [None, 12, 3.5, True, b"Beverages", "", "   ", "\t\n"],
)
def test_category_name_is_required_and_must_be_a_string(name):
    with pytest.raises(ValueError, match="name"):
        validate_category_creation(make_principal(), name=name)


def test_category_name_accepts_120_characters_after_trimming():
    name = "x" * CATEGORY_NAME_MAX_LENGTH

    assert validate_category_creation(
        make_principal(), name=f"  {name}  "
    ).data.name == name


@pytest.mark.parametrize("extra_whitespace", ["", "  "])
def test_category_name_rejects_more_than_120_normalized_characters(extra_whitespace):
    name = f"{'x' * (CATEGORY_NAME_MAX_LENGTH + 1)}{extra_whitespace}"

    with pytest.raises(ValueError, match="at most 120 characters"):
        validate_category_creation(make_principal(), name=name)


@pytest.mark.parametrize("name", [None, 12, "", "   ", "x" * 121])
def test_category_modification_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="name"):
        validate_category_modification(
            make_principal(),
            category_id=7,
            category=category(),
            changes={"name": name},
        )


def test_duplicate_category_names_are_normalized_within_business_only():
    existing = [
        category(name="  Beverages "),
        category(business_id=99, name="Other business category"),
    ]

    with pytest.raises(ValueError, match="already used"):
        ensure_category_name_available(10, "beverages", existing)

    assert ensure_category_name_available(99, "BEVERAGES", existing) == "BEVERAGES"
    assert ensure_category_name_available(10, "Snacks", existing) == "Snacks"


def test_category_modification_excludes_its_existing_name_and_rejects_duplicates():
    current = category()
    proposal = validate_category_modification(
        make_principal(),
        category_id=7,
        category=current,
        changes={"name": "  Hot Drinks "},
        existing_categories=[current, category(category_id=8, name="Cold Drinks")],
    )

    assert proposal.data.name == "Hot Drinks"
    with pytest.raises(ValueError, match="already used"):
        validate_category_modification(
            make_principal(),
            category_id=7,
            category=current,
            changes={"name": " cold drinks "},
            existing_categories=[current, category(category_id=8, name="Cold Drinks")],
        )


def test_category_ids_and_server_loaded_context_must_match():
    with pytest.raises(ValueError, match="positive integer"):
        validate_category_modification(
            make_principal(),
            category_id=True,
            category=category(),
            changes={"name": "Other"},
        )
    with pytest.raises(ValueError, match="does not match"):
        validate_category_modification(
            make_principal(),
            category_id=8,
            category=category(category_id=7),
            changes={"name": "Other"},
        )


def test_foreign_business_category_cannot_be_modified_or_toggled():
    with pytest.raises(PermissionError):
        validate_category_modification(
            make_principal(),
            category_id=7,
            category=category(business_id=99),
            changes={"name": "Other"},
        )
    with pytest.raises(PermissionError):
        validate_category_deactivation(
            make_principal(), category_id=7, category=category(business_id=99)
        )


def test_branch_manager_cannot_directly_change_shared_category():
    manager = make_principal(Role.BRANCH_MANAGER)

    with pytest.raises(PermissionError):
        validate_category_creation(manager, name="Beverages")
    with pytest.raises(PermissionError):
        validate_category_activation(manager, category_id=7, category=category())


def test_only_authorized_owner_can_directly_manage_categories():
    employee = make_principal(Role.EMPLOYEE)
    with pytest.raises(PermissionError):
        validate_category_creation(employee, name="Beverages")
    with pytest.raises(PermissionError):
        validate_category_modification(
            employee,
            category_id=7,
            category=category(),
            changes={"name": "Other"},
        )
    with pytest.raises(PermissionError):
        validate_category_deactivation(
            employee, category_id=7, category=category()
        )


def test_owner_without_catalog_manage_permission_cannot_change_category():
    owner = make_principal(Role.OWNER, permissions=set())

    with pytest.raises(PermissionError):
        validate_category_creation(owner, name="Beverages")


def test_category_modification_rejects_inventory_or_unknown_fields():
    with pytest.raises(ValueError, match="unsupported"):
        validate_category_modification(
            make_principal(),
            category_id=7,
            category=category(),
            changes={"available_qty": 5},
        )


@pytest.mark.parametrize("is_active", [1, 0, None, "false"])
def test_category_status_requires_a_boolean(is_active):
    with pytest.raises(ValueError, match="boolean"):
        validate_category_status_change(
            make_principal(),
            category_id=7,
            category=category(),
            is_active=is_active,
        )


def test_category_activation_and_deactivation_are_metadata_only_proposals():
    product_references = [{"product_id": 501, "category_id": 7}]
    before = [record.copy() for record in product_references]

    deactivated = validate_category_deactivation(
        make_principal(), category_id=7, category=category()
    )
    activated = validate_category_activation(
        make_principal(), category_id=7, category=category(is_active=False)
    )

    assert deactivated == CategoryStatusProposal(is_active=False)
    assert activated == CategoryStatusProposal(is_active=True)
    assert product_references == before
    assert not hasattr(__import__("category_rules"), "validate_category_deletion")



