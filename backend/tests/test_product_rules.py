"""Tests for pure product metadata and shared catalog rules."""

from decimal import Decimal

import pytest

from permissions import (
    PRODUCT_CATALOG_APPROVAL,
    PRODUCT_REQUEST_SUBMIT,
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from product_request_rules import (
    ProductChangeType,
    ProductRequestStatus,
    validate_request_approval,
)
from product_rules import (
    PRODUCT_CODE_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    CatalogCodeEntry,
    ProductAction,
    ProductCategoryContext,
    ProductData,
    ProductProposal,
    ProductStatusProposal,
    ensure_catalog_code_available,
    require_product_catalog_access,
    validate_product_activation,
    validate_product_creation,
    validate_product_data,
    validate_product_deactivation,
    validate_product_modification,
    validate_product_status_change,
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


def category(category_id=7, business_id=10, branch_id=None, is_active=True):
    return ProductCategoryContext(
        category_id=category_id,
        scope=ResourceScope(business_id=business_id, branch_id=branch_id),
        is_active=is_active,
    )


def product_scope(business_id=10):
    return ResourceScope(business_id=business_id, branch_id=None)


def product_data(**changes):
    fields = {
        "business_id": 10,
        "name": "Green Tea",
        "product_code": "TEA-01",
        "category_id": 7,
        "selling_price": "2.500",
        "category": category(),
    }
    fields.update(changes)
    return validate_product_data(**fields)


def entry(code, resource_type="product", business_id=10, resource_id=31):
    return CatalogCodeEntry(
        business_id=business_id,
        code=code,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def test_required_fields_are_trimmed_and_cost_price_defaults_to_zero():
    data = product_data(name="  Green Tea  ", product_code=" TEA-01 ")

    assert data == ProductData(
        name="Green Tea",
        product_code="TEA-01",
        category_id=7,
        selling_price=Decimal("2.500"),
        cost_price=Decimal("0.000"),
    )


@pytest.mark.parametrize("name", [None, 12, "", "   ", "n" * (PRODUCT_NAME_MAX_LENGTH + 1)])
def test_invalid_or_oversized_product_names_are_rejected(name):
    with pytest.raises(ValueError):
        product_data(name=name)


@pytest.mark.parametrize(
    "product_code",
    [None, 12, "", "   ", "c" * (PRODUCT_CODE_MAX_LENGTH + 1)],
)
def test_invalid_or_oversized_product_codes_are_rejected(product_code):
    with pytest.raises(ValueError):
        product_data(product_code=product_code)


@pytest.mark.parametrize("category_id", [None, 0, -1, True, 7.0, "7"])
def test_category_id_must_be_a_positive_integer(category_id):
    with pytest.raises(ValueError, match="category_id"):
        product_data(category_id=category_id)


def test_category_context_must_match_the_id_and_business():
    with pytest.raises(ValueError, match="does not match"):
        product_data(category=category(category_id=8))
    with pytest.raises(ValueError, match="same business"):
        product_data(category=category(business_id=99))
    with pytest.raises(ValueError, match="trusted category context"):
        product_data(category={"category_id": 7, "business_id": 10})


def test_product_creation_requires_a_trusted_active_category():
    with pytest.raises(ValueError, match="inactive category"):
        product_data(category=category(is_active=False))
    with pytest.raises(ValueError, match="is_active"):
        product_data(category=category(is_active=1))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selling_price", -1),
        ("selling_price", "NaN"),
        ("selling_price", "Infinity"),
        ("selling_price", "1000000000"),
        ("selling_price", "1.2341"),
        ("selling_price", "1_000"),
        ("selling_price", 0.1),
        ("selling_price", True),
        ("cost_price", -0.001),
        ("cost_price", "0.0001"),
        ("cost_price", 0.1),
    ],
)
def test_prices_must_be_nonnegative_exact_decimal_12_3(field, value):
    with pytest.raises(ValueError):
        product_data(**{field: value})


def test_exact_decimal_values_are_normalized_without_float_rounding():
    data = product_data(selling_price=Decimal("999999999.999"), cost_price="1.2300")

    assert data.selling_price == Decimal("999999999.999")
    assert data.cost_price == Decimal("1.230")


@pytest.mark.parametrize("existing_type", ["product", "variant"])
def test_product_code_collides_with_any_code_in_shared_business_registry(existing_type):
    with pytest.raises(ValueError, match="already used"):
        product_data(catalog_codes=[entry("TEA-01", resource_type=existing_type)])


def test_product_code_may_be_reused_in_another_business():
    data = product_data(catalog_codes=[entry("TEA-01", business_id=99)])

    assert data.product_code == "TEA-01"


def test_catalog_code_comparison_is_case_insensitive_and_excludes_current_product():
    with pytest.raises(ValueError, match="already used"):
        product_data(catalog_codes=[entry("tea-01", resource_type="variant")])

    code = ensure_catalog_code_available(
        10,
        "TEA-01",
        [entry("TEA-01", resource_type="product", resource_id=50)],
        exclude_resource_type="product",
        exclude_resource_id=50,
    )
    assert code == "TEA-01"


def test_owner_can_validate_product_creation_but_it_creates_no_record():
    proposal = validate_product_creation(
        make_principal(),
        name="Green Tea",
        product_code="TEA-01",
        category_id=7,
        selling_price="2.500",
        category=category(),
    )

    assert isinstance(proposal, ProductProposal)
    assert proposal.data.name == "Green Tea"
    assert proposal.request is None


def test_employee_must_submit_product_creation_as_a_request():
    employee = make_principal(
        Role.EMPLOYEE,
        permissions={PRODUCT_REQUEST_SUBMIT},
    )
    fields = {
        "name": "Green Tea",
        "product_code": "TEA-01",
        "category_id": 7,
        "selling_price": "2.500",
        "category": category(),
    }

    with pytest.raises(PermissionError):
        validate_product_creation(employee, **fields)

    proposal = validate_product_creation(employee, is_request=True, **fields)
    assert proposal.request.change_type is ProductChangeType.ADD_PRODUCT
    assert proposal.request.is_shared_catalog is True
    assert proposal.request.status is ProductRequestStatus.PENDING


def test_branch_manager_cannot_modify_shared_catalog_directly_or_submit_employee_request():
    manager = make_principal(Role.BRANCH_MANAGER)

    with pytest.raises(PermissionError):
        require_product_catalog_access(manager, ProductAction.CREATE)
    with pytest.raises(PermissionError):
        require_product_catalog_access(manager, ProductAction.CREATE, is_request=True)


def test_employee_request_preserves_existing_same_business_owner_approval_rule():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})
    proposal = validate_product_creation(
        employee,
        name="Green Tea",
        product_code="TEA-01",
        category_id=7,
        selling_price="2.500",
        category=category(),
        is_request=True,
    )

    validate_request_approval(make_principal(), proposal.request)
    foreign_owner = make_principal(
        Role.OWNER,
        business_id=99,
        permissions={PRODUCT_CATALOG_APPROVAL},
    )
    with pytest.raises(PermissionError):
        validate_request_approval(foreign_owner, proposal.request)


def test_owner_must_have_catalog_management_grant():
    owner_without_grant = make_principal(Role.OWNER, permissions=set())

    with pytest.raises(PermissionError):
        require_product_catalog_access(owner_without_grant, ProductAction.CREATE)


def test_modification_returns_a_proposal_without_mutating_current_metadata():
    current = product_data()
    before = current
    proposal = validate_product_modification(
        make_principal(),
        product_id=50,
        product_scope=product_scope(),
        current_data=current,
        changes={"selling_price": "3.250", "name": "New Green Tea"},
        category=category(),
        catalog_codes=[entry("TEA-01", resource_type="product", resource_id=50)],
    )

    assert proposal.data.name == "New Green Tea"
    assert proposal.data.selling_price == Decimal("3.250")
    assert current is before
    assert current.name == "Green Tea"


def test_modification_rejects_foreign_products_and_foreign_categories():
    with pytest.raises(PermissionError):
        validate_product_modification(
            make_principal(),
            product_id=50,
            product_scope=product_scope(business_id=99),
            current_data=product_data(),
            changes={"name": "Other"},
            category=category(business_id=99),
        )
    with pytest.raises(ValueError, match="same business"):
        validate_product_modification(
            make_principal(),
            product_id=50,
            product_scope=product_scope(),
            current_data=product_data(),
            changes={"category_id": 8},
            category=category(category_id=8, business_id=99),
        )


def test_modification_cannot_assign_an_inactive_category():
    with pytest.raises(ValueError, match="inactive category"):
        validate_product_modification(
            make_principal(),
            product_id=50,
            product_scope=product_scope(),
            current_data=product_data(),
            changes={"category_id": 8},
            category=category(category_id=8, is_active=False),
        )


def test_unrelated_modification_preserves_historical_inactive_category():
    proposal = validate_product_modification(
        make_principal(),
        product_id=50,
        product_scope=product_scope(),
        current_data=product_data(),
        changes={"name": "Renamed Green Tea"},
        category=category(is_active=False),
    )

    assert proposal.data.category_id == 7
    assert proposal.data.name == "Renamed Green Tea"


def test_modification_cannot_change_inventory_or_unknown_metadata():
    with pytest.raises(ValueError, match="unsupported"):
        validate_product_modification(
            make_principal(),
            product_id=50,
            product_scope=product_scope(),
            current_data=product_data(),
            changes={"available_qty": 100},
            category=category(),
        )


def test_modification_checks_new_product_code_against_variant_registry():
    with pytest.raises(ValueError, match="already used"):
        validate_product_modification(
            make_principal(),
            product_id=50,
            product_scope=product_scope(),
            current_data=product_data(),
            changes={"product_code": "VAR-01"},
            category=category(),
            catalog_codes=[
                entry("TEA-01", "product", resource_id=50),
                entry("VAR-01", "variant", resource_id=901),
            ],
        )


def test_employee_product_modification_is_pending_request_not_direct_write():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})
    proposal = validate_product_modification(
        employee,
        product_id=50,
        product_scope=product_scope(),
        current_data=product_data(),
        changes={"selling_price": "3.000"},
        category=category(),
        catalog_codes=[entry("TEA-01", resource_type="product", resource_id=50)],
        is_request=True,
    )

    assert proposal.data.selling_price == Decimal("3.000")
    assert proposal.request.change_type is ProductChangeType.MODIFY_PRODUCT
    assert proposal.request.status is ProductRequestStatus.PENDING


def test_activation_and_deactivation_are_owner_gated_boolean_proposals():
    owner = make_principal()
    deactivation = validate_product_deactivation(
        owner,
        product_id=50,
        product_scope=product_scope(),
    )
    activation = validate_product_activation(
        owner,
        product_id=50,
        product_scope=product_scope(),
    )

    assert deactivation == ProductStatusProposal(is_active=False)
    assert activation.is_active is True
    with pytest.raises(ValueError, match="boolean"):
        validate_product_status_change(
            owner,
            product_id=50,
            product_scope=product_scope(),
            is_active=1,
        )


def test_employee_deactivation_becomes_a_pending_modification_request():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})
    proposal = validate_product_deactivation(
        employee,
        product_id=50,
        product_scope=product_scope(),
        is_request=True,
    )

    assert proposal.is_active is False
    assert proposal.request.change_type is ProductChangeType.MODIFY_PRODUCT
    assert proposal.request.status is ProductRequestStatus.PENDING


def test_deactivation_rejects_foreign_business_and_products_cannot_be_deleted():
    with pytest.raises(PermissionError):
        validate_product_deactivation(
            make_principal(),
            product_id=50,
            product_scope=product_scope(business_id=99),
        )
    with pytest.raises(ValueError, match="cannot be permanently deleted"):
        require_product_catalog_access(make_principal(), ProductAction.DELETE)

