"""Tests for pure POS sale preparation; no database or inventory is changed."""

from decimal import Decimal

import pytest

from inventory_movement_rules import MovementType
from permissions import (
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from sale_rules import (
    ProposedSaleDeduction,
    SaleLineItem,
    SaleProductContext,
    SaleVariantContext,
    build_sale_inventory_movements,
    prepare_sale,
)


BUSINESS_ID = 10
BRANCH_ID = 22
ACTOR_ID = 701
PRODUCT_ID = 50
VARIANT_ID = 101
_DEFAULT_ITEMS = object()


def principal(
    role=Role.BRANCH_MANAGER,
    *,
    business_id=BUSINESS_ID,
    branch_id=BRANCH_ID,
    permissions=None,
):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=(
            ROLE_PERMISSIONS[role]
            if permissions is None
            else frozenset(permissions)
        ),
    )


def branch_scope(branch_id=BRANCH_ID, business_id=BUSINESS_ID):
    return ResourceScope(business_id=business_id, branch_id=branch_id)


def business_scope(business_id=BUSINESS_ID):
    return ResourceScope(business_id=business_id, branch_id=None)


def product(
    product_id=PRODUCT_ID,
    *,
    business_id=BUSINESS_ID,
    selling_price=Decimal("2.500"),
    is_active=True,
):
    return SaleProductContext(
        product_id=product_id,
        scope=business_scope(business_id),
        selling_price=selling_price,
        is_active=is_active,
    )


def variant(
    variant_id=VARIANT_ID,
    *,
    product_id=PRODUCT_ID,
    business_id=BUSINESS_ID,
    selling_price=None,
    is_active=True,
):
    return SaleVariantContext(
        variant_id=variant_id,
        product_id=product_id,
        scope=business_scope(business_id),
        selling_price=selling_price,
        is_active=is_active,
    )


def inventory(variant_id=VARIANT_ID, *, branch_id=BRANCH_ID, available_qty=20):
    return {
        "branch_id": branch_id,
        "product_variant_id": variant_id,
        "available_qty": available_qty,
    }


def prepare(items=_DEFAULT_ITEMS, **changes):
    values = {
        "principal": principal(),
        "actor_id": ACTOR_ID,
        "branch_scope": branch_scope(),
        "items": [
            {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 2}
        ] if items is _DEFAULT_ITEMS else items,
        "products": {PRODUCT_ID: product()},
        "variants": {VARIANT_ID: variant()},
        "inventory_records": {(BRANCH_ID, VARIANT_ID): inventory()},
    }
    values.update(changes)
    return prepare_sale(**values)


def test_valid_single_item_sale_uses_trusted_server_price():
    proposal = prepare(
        [
            {
                "product_id": PRODUCT_ID,
                "product_variant_id": VARIANT_ID,
                "quantity": 2,
                "unit_price": "0.001",
            }
        ]
    )

    assert proposal.items == (
        SaleLineItem(
            product_id=PRODUCT_ID,
            product_variant_id=VARIANT_ID,
            quantity=2,
            unit_price=Decimal("2.500"),
            subtotal=Decimal("5.000"),
        ),
    )
    assert proposal.business_id == BUSINESS_ID
    assert proposal.branch_id == BRANCH_ID
    assert proposal.responsible_user_id == ACTOR_ID
    assert proposal.total_amount == Decimal("5.000")
    assert proposal.sale_reference is None
    assert proposal.request_idempotency_key is None


def test_multiple_products_variants_and_variant_price_override_are_supported():
    proposal = prepare(
        [
            {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 1},
            {"product_id": 51, "product_variant_id": 102, "quantity": 3},
        ],
        products={
            PRODUCT_ID: product(selling_price=Decimal("2.000")),
            51: product(51, selling_price=Decimal("7.000")),
        },
        variants={
            VARIANT_ID: variant(selling_price=Decimal("2.125")),
            102: variant(102, product_id=51),
        },
        inventory_records={
            (BRANCH_ID, VARIANT_ID): inventory(available_qty=5),
            (BRANCH_ID, 102): inventory(102, available_qty=5),
        },
    )

    assert [(line.unit_price, line.subtotal) for line in proposal.items] == [
        (Decimal("2.125"), Decimal("2.125")),
        (Decimal("7.000"), Decimal("21.000")),
    ]
    assert proposal.total_amount == Decimal("23.125")
    assert proposal.inventory_deductions == (
        ProposedSaleDeduction(VARIANT_ID, 1),
        ProposedSaleDeduction(102, 3),
    )


def test_subtotals_and_total_preserve_three_decimal_precision():
    proposal = prepare(
        [
            {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 3},
            {"product_id": 51, "product_variant_id": 102, "quantity": 2},
        ],
        products={
            PRODUCT_ID: product(selling_price=Decimal("0.125")),
            51: product(51, selling_price=Decimal("0.001")),
        },
        variants={VARIANT_ID: variant(), 102: variant(102, product_id=51)},
        inventory_records={
            (BRANCH_ID, VARIANT_ID): inventory(available_qty=5),
            (BRANCH_ID, 102): inventory(102, available_qty=5),
        },
    )

    assert [item.subtotal for item in proposal.items] == [
        Decimal("0.375"),
        Decimal("0.002"),
    ]
    assert proposal.total_amount == Decimal("0.377")


def test_duplicate_cart_lines_are_aggregated_before_stock_validation():
    lines = [
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 2},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 3},
    ]
    proposal = prepare(lines)
    movements = build_sale_inventory_movements(
        principal(),
        actor_id=ACTOR_ID,
        sale_id=501,
        proposal=proposal,
        branch_scope=branch_scope(),
        variant_scopes={VARIANT_ID: (business_scope(), business_scope())},
    )

    assert len(proposal.items) == 2
    assert proposal.inventory_deductions == (
        ProposedSaleDeduction(VARIANT_ID, 5),
    )
    assert len(movements) == 1
    assert movements[0].movement_type is MovementType.SALE
    assert movements[0].quantity_delta == -5
    assert movements[0].source_reference == "501"


def test_duplicate_cart_lines_cannot_exceed_available_stock():
    with pytest.raises(ValueError, match="exceeds available stock"):
        prepare(
            [
                {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 3},
                {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 3},
            ],
            inventory_records={(BRANCH_ID, VARIANT_ID): inventory(available_qty=5)},
        )


def test_insufficient_stock_is_rejected():
    with pytest.raises(ValueError, match="exceeds available stock"):
        prepare(
            inventory_records={(BRANCH_ID, VARIANT_ID): inventory(available_qty=1)}
        )


def test_missing_stock_record_is_zero_and_rejected_for_a_sale():
    with pytest.raises(ValueError, match="exceeds available stock"):
        prepare(inventory_records={})


@pytest.mark.parametrize("bad_items", [[], (), None, "cart"])
def test_empty_or_non_collection_cart_is_rejected(bad_items):
    with pytest.raises(ValueError):
        prepare(bad_items)


@pytest.mark.parametrize(
    "item",
    [
        None,
        [],
        {"product_variant_id": VARIANT_ID, "quantity": 1},
        {"product_id": PRODUCT_ID, "quantity": 1},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID},
        {"product_id": True, "product_variant_id": VARIANT_ID, "quantity": 1},
        {"product_id": PRODUCT_ID, "product_variant_id": False, "quantity": 1},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": True},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 0},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": -1},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 1.5},
        {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": "2"},
    ],
)
def test_malformed_lines_and_invalid_ids_or_quantities_are_rejected(item):
    with pytest.raises(ValueError):
        prepare([item])


@pytest.mark.parametrize(
    "bad_price",
    [True, 1.2, -1, "NaN", "Infinity", "1.2345", "1000000000.000", "not-money"],
)
def test_invalid_trusted_prices_are_rejected(bad_price):
    with pytest.raises(ValueError):
        prepare(products={PRODUCT_ID: product(selling_price=bad_price)})


def test_line_subtotal_overflow_is_rejected():
    with pytest.raises(ValueError, match=r"DECIMAL\(12,3\)"):
        prepare(
            [{"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 2}],
            products={PRODUCT_ID: product(selling_price="999999999.999")},
        )


def test_total_overflow_is_rejected():
    with pytest.raises(ValueError, match=r"DECIMAL\(12,3\)"):
        prepare(
            [
                {"product_id": PRODUCT_ID, "product_variant_id": VARIANT_ID, "quantity": 1},
                {"product_id": 51, "product_variant_id": 102, "quantity": 1},
            ],
            products={
                PRODUCT_ID: product(selling_price="600000000.000"),
                51: product(51, selling_price="600000000.000"),
            },
            variants={VARIANT_ID: variant(), 102: variant(102, product_id=51)},
            inventory_records={
                (BRANCH_ID, VARIANT_ID): inventory(),
                (BRANCH_ID, 102): inventory(102),
            },
        )


def test_missing_variant_or_product_context_is_rejected():
    with pytest.raises(ValueError, match="product context"):
        prepare(products={})
    with pytest.raises(ValueError, match="variant context"):
        prepare(variants={})


def test_variant_must_belong_to_the_cart_product():
    with pytest.raises(ValueError, match="does not belong"):
        prepare(variants={VARIANT_ID: variant(product_id=51)})


def test_unauthorized_actor_cannot_record_sales():
    with pytest.raises(PermissionError):
        prepare(principal=principal(permissions=set()))


def test_employee_without_sales_record_permission_is_denied():
    with pytest.raises(PermissionError):
        prepare(principal=principal(Role.EMPLOYEE, permissions=set()))


def test_cross_business_product_or_variant_context_is_denied():
    with pytest.raises(PermissionError):
        prepare(products={PRODUCT_ID: product(business_id=99)})
    with pytest.raises(PermissionError):
        prepare(variants={VARIANT_ID: variant(business_id=99)})


def test_actor_must_be_authorized_for_the_sale_branch():
    with pytest.raises(PermissionError):
        prepare(principal=principal(branch_id=23))


@pytest.mark.parametrize(
    "product_context,variant_context",
    [
        (product(is_active=False), variant()),
        (product(), variant(is_active=False)),
    ],
)
def test_inactive_products_and_variants_cannot_be_sold(
    product_context, variant_context
):
    with pytest.raises(ValueError, match="Inactive"):
        prepare(
            products={PRODUCT_ID: product_context},
            variants={VARIANT_ID: variant_context},
        )


def test_proposal_movement_builder_requires_same_actor_and_branch():
    proposal = prepare()
    args = {
        "sale_id": 501,
        "proposal": proposal,
        "branch_scope": branch_scope(),
        "variant_scopes": {VARIANT_ID: (business_scope(), business_scope())},
    }
    with pytest.raises(PermissionError):
        build_sale_inventory_movements(principal(), actor_id=ACTOR_ID + 1, **args)
    with pytest.raises(ValueError, match="branch scope"):
        build_sale_inventory_movements(
            principal(),
            actor_id=ACTOR_ID,
            **{**args, "branch_scope": branch_scope(23)},
        )


def test_inventory_movement_builder_requires_a_server_assigned_sale_id():
    proposal = prepare()
    with pytest.raises(ValueError, match="sale_id"):
        build_sale_inventory_movements(
            principal(),
            actor_id=ACTOR_ID,
            sale_id=None,
            proposal=proposal,
            branch_scope=branch_scope(),
            variant_scopes={VARIANT_ID: (business_scope(), business_scope())},
        )


def test_preparing_sale_and_movements_does_not_change_inventory_record():
    record = inventory(available_qty=9)
    prepare(inventory_records={(BRANCH_ID, VARIANT_ID): record})

    assert record["available_qty"] == 9
