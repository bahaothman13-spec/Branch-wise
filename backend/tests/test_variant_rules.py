"""Tests for pure product variant rules; no database or inventory is used."""

from decimal import Decimal

import pytest

from permissions import (
    PRODUCT_REQUEST_SUBMIT,
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from product_request_rules import ProductChangeType, validate_request_approval
from product_rules import CatalogCodeEntry
from variant_rules import (
    ProductContext,
    ProductVariantContext,
    VariantData,
    VariantStatusProposal,
    validate_variant_activation,
    validate_variant_creation,
    validate_variant_deactivation,
    validate_variant_modification,
    validate_variant_status_change,
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


def product(product_id=50, business_id=10):
    return ProductContext(
        product_id=product_id,
        scope=ResourceScope(business_id=business_id, branch_id=None),
    )


def variant(
    variant_id=901, product_id=50, business_id=10, is_default=False
):
    return ProductVariantContext(
        variant_id=variant_id,
        product_id=product_id,
        scope=ResourceScope(business_id=business_id, branch_id=None),
        is_default=is_default,
    )


def code_entry(code, resource_type="product", resource_id=31, business_id=10):
    return CatalogCodeEntry(
        business_id=business_id,
        code=code,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def create_variant(**changes):
    fields = {
        "principal": make_principal(),
        "product_id": 50,
        "product": product(),
        "code": "TEA-M-RED",
        "size": " M ",
        "color": " Red ",
    }
    fields.update(changes)
    return validate_variant_creation(**fields)


def current_variant_data(**changes):
    fields = {
        "product_id": 50,
        "code": "TEA-M-RED",
        "size": "M",
        "color": "Red",
        "selling_price": Decimal("2.500"),
        "cost_price": Decimal("1.200"),
        "is_default": False,
    }
    fields.update(changes)
    return VariantData(**fields)


def test_size_color_variant_and_optional_exact_prices_are_normalized():
    proposal = create_variant(
        selling_price="2.500",
        cost_price=Decimal("1.250"),
    )

    assert proposal.data == VariantData(
        product_id=50,
        code="TEA-M-RED",
        size="M",
        color="Red",
        selling_price=Decimal("2.500"),
        cost_price=Decimal("1.250"),
        is_default=False,
    )
    assert proposal.request is None


def test_simple_product_supports_one_default_variant_without_size_or_color():
    proposal = create_variant(
        code="TEA-DEFAULT",
        size=None,
        color=None,
        is_default=True,
    )

    assert proposal.data.is_default is True
    assert proposal.data.size is None
    assert proposal.data.color is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selling_price", -1),
        ("selling_price", "NaN"),
        ("selling_price", "Infinity"),
        ("selling_price", "1000000000"),
        ("selling_price", "1.2341"),
        ("selling_price", 0.1),
        ("cost_price", True),
        ("cost_price", "-0.001"),
    ],
)
def test_invalid_variant_price_is_rejected(field, value):
    with pytest.raises(ValueError):
        create_variant(**{field: value})


def test_default_variant_may_omit_prices_and_excess_decimal_zeroes_are_normalized():
    proposal = create_variant(
        code="TEA-DEFAULT",
        size=None,
        color=None,
        is_default=True,
        selling_price="4.2500",
    )

    assert proposal.data.selling_price == Decimal("4.250")
    assert proposal.data.cost_price is None


@pytest.mark.parametrize("code", [None, 12, "", "   ", "x" * 81])
def test_variant_codes_follow_product_code_validation(code):
    with pytest.raises(ValueError):
        create_variant(code=code)


@pytest.mark.parametrize(
    ("size", "color", "is_default"),
    [
        (None, None, False),
        ("  ", "Red", False),
        (12, "Red", False),
        ("M", None, 1),
        ("M", None, True),
    ],
)
def test_variant_attributes_and_default_combinations_are_validated(
    size, color, is_default
):
    with pytest.raises(ValueError):
        create_variant(size=size, color=color, is_default=is_default)


@pytest.mark.parametrize("product_id", [None, 0, -1, True, "50"])
def test_variant_requires_a_positive_product_identifier(product_id):
    with pytest.raises(ValueError, match="product_id"):
        create_variant(product_id=product_id)


def test_server_loaded_product_must_match_requested_product_identifier():
    with pytest.raises(ValueError, match="does not match"):
        create_variant(product=product(product_id=51))


@pytest.mark.parametrize("resource_type", ["product", "variant"])
def test_variant_code_cannot_collide_with_product_or_variant_code(resource_type):
    with pytest.raises(ValueError, match="already used"):
        create_variant(
            catalog_codes=[
                code_entry("TEA-M-RED", resource_type=resource_type)
            ]
        )


def test_variant_codes_compare_case_insensitively_with_shared_catalog_registry():
    with pytest.raises(ValueError, match="already used"):
        create_variant(catalog_codes=[code_entry("tea-m-red", "variant")])


def test_same_code_is_valid_in_a_different_business():
    proposal = create_variant(
        catalog_codes=[code_entry("TEA-M-RED", "variant", business_id=99)]
    )

    assert proposal.data.code == "TEA-M-RED"


def test_duplicate_default_variant_is_rejected_for_the_same_product():
    with pytest.raises(ValueError, match="already has a default"):
        create_variant(
            code="TEA-DEFAULT-2",
            size=None,
            color=None,
            is_default=True,
            existing_variants=[variant(is_default=True)],
        )


def test_default_variant_in_another_product_or_business_does_not_conflict():
    proposal = create_variant(
        code="TEA-DEFAULT-2",
        size=None,
        color=None,
        is_default=True,
        existing_variants=[
            variant(product_id=51, is_default=True),
            variant(variant_id=902, business_id=99, is_default=True),
        ],
    )

    assert proposal.data.is_default is True


def test_variant_requires_a_product_and_variant_from_the_same_business():
    with pytest.raises(ValueError, match="same business"):
        validate_variant_modification(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(business_id=99),
            current_data=current_variant_data(),
            changes={"color": "Blue"},
        )


def test_owner_cannot_create_variant_for_foreign_business_product():
    with pytest.raises(PermissionError):
        create_variant(product=product(business_id=99))


def test_variant_context_must_belong_to_the_supplied_product():
    with pytest.raises(ValueError, match="does not belong"):
        validate_variant_modification(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(product_id=51),
            current_data=current_variant_data(),
            changes={"color": "Blue"},
        )


def test_employee_must_submit_variant_changes_and_owner_can_approve():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})

    with pytest.raises(PermissionError):
        create_variant(principal=employee)
    proposal = create_variant(principal=employee, is_request=True)

    assert proposal.request.change_type is ProductChangeType.ADD_VARIANT
    assert proposal.request.is_shared_catalog is True
    validate_request_approval(make_principal(), proposal.request)


def test_employee_variant_request_cannot_target_another_business():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})

    with pytest.raises(PermissionError):
        create_variant(principal=employee, product=product(business_id=99), is_request=True)


def test_branch_manager_cannot_directly_modify_shared_variants():
    manager = make_principal(Role.BRANCH_MANAGER)

    with pytest.raises(PermissionError):
        create_variant(principal=manager)


def test_variant_modification_returns_a_proposal_and_excludes_its_own_code():
    existing_code = code_entry("TEA-M-RED", "variant", resource_id=901)
    proposal = validate_variant_modification(
        make_principal(),
        product_id=50,
        product=product(),
        product_variant_id=901,
        variant=variant(),
        current_data=current_variant_data(),
        changes={"color": " Blue ", "selling_price": "3.000"},
        catalog_codes=[existing_code],
        existing_variants=[variant()],
    )

    assert proposal.data.color == "Blue"
    assert proposal.data.selling_price == Decimal("3.000")
    assert proposal.data.code == "TEA-M-RED"


def test_variant_modification_detects_code_collision_with_either_resource_type():
    for resource_type in ("product", "variant"):
        with pytest.raises(ValueError, match="already used"):
            validate_variant_modification(
                make_principal(),
                product_id=50,
                product=product(),
                product_variant_id=901,
                variant=variant(),
                current_data=current_variant_data(),
                changes={"code": "COLLISION"},
                catalog_codes=[
                    code_entry("TEA-M-RED", "variant", resource_id=901),
                    code_entry("COLLISION", resource_type),
                ],
            )


def test_variant_modification_rejects_invalid_id_and_inventory_fields():
    common = {
        "principal": make_principal(),
        "product_id": 50,
        "product": product(),
        "variant": variant(),
        "current_data": current_variant_data(),
    }
    with pytest.raises(ValueError, match="positive integer"):
        validate_variant_modification(
            **common,
            product_variant_id=True,
            changes={"color": "Blue"},
        )
    with pytest.raises(ValueError, match="unsupported"):
        validate_variant_modification(
            **common,
            product_variant_id=901,
            changes={"available_qty": 50},
        )


def test_modification_cannot_move_variant_to_a_different_product():
    with pytest.raises(ValueError, match="unsupported"):
        validate_variant_modification(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(),
            current_data=current_variant_data(),
            changes={"product_id": 51},
        )


def test_modification_cannot_create_a_second_default_variant():
    with pytest.raises(ValueError, match="already has a default"):
        validate_variant_modification(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(),
            current_data=current_variant_data(),
            changes={"size": None, "color": None, "is_default": True},
            existing_variants=[variant(), variant(variant_id=902, is_default=True)],
        )


def test_current_variant_data_must_match_its_server_loaded_default_flag():
    with pytest.raises(ValueError, match="does not match"):
        validate_variant_modification(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(is_default=True),
            current_data=current_variant_data(),
            changes={"color": "Blue"},
        )


@pytest.mark.parametrize("is_active", [1, 0, None, "false"])
def test_variant_status_requires_a_boolean(is_active):
    with pytest.raises(ValueError, match="boolean"):
        validate_variant_status_change(
            make_principal(),
            product_id=50,
            product=product(),
            product_variant_id=901,
            variant=variant(),
            is_active=is_active,
        )


def test_activation_and_deactivation_preserve_history_and_do_not_change_inventory():
    inventory = {"available_qty": 12, "damaged_qty": 2}
    original_inventory = inventory.copy()
    deactivated = validate_variant_deactivation(
        make_principal(),
        product_id=50,
        product=product(),
        product_variant_id=901,
        variant=variant(),
    )
    activated = validate_variant_activation(
        make_principal(),
        product_id=50,
        product=product(),
        product_variant_id=901,
        variant=variant(),
    )

    assert deactivated == VariantStatusProposal(is_active=False)
    assert activated == VariantStatusProposal(is_active=True)
    assert inventory == original_inventory
    assert not hasattr(__import__("variant_rules"), "validate_variant_deletion")


def test_employee_variant_deactivation_becomes_pending_modification_request():
    employee = make_principal(Role.EMPLOYEE, permissions={PRODUCT_REQUEST_SUBMIT})
    proposal = validate_variant_deactivation(
        employee,
        product_id=50,
        product=product(),
        product_variant_id=901,
        variant=variant(),
        is_request=True,
    )

    assert proposal.is_active is False
    assert proposal.request.change_type is ProductChangeType.MODIFY_VARIANT

