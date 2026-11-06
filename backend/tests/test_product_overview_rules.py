"""Read-only product overview rules using trusted in-memory records."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from category_rules import CategoryContext
from inventory_movement_rules import (
    MovementOrigin,
    MovementSource,
    MovementType,
    ProposedInventoryMovement,
)
from permissions import (
    INVENTORY_VIEW,
    PRODUCTS_VIEW,
    ROLE_PERMISSIONS,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
)
from product_overview_rules import (
    BranchOverviewContext,
    InventoryMovementHistoryRecord,
    ProductOverviewVariantRecord,
    build_product_overview,
)
from product_rules import ProductCategoryContext, ProductData, validate_product_data
from variant_rules import ProductContext, ProductVariantContext, VariantData


BUSINESS_ID = 10
PRODUCT_ID = 50
CATEGORY_ID = 7
DEFAULT_VARIANT_ID = 101
SECOND_VARIANT_ID = 102
BRANCH_ID = 22
OTHER_BRANCH_ID = 23


def principal(role=Role.OWNER, *, business_id=BUSINESS_ID, branch_id=BRANCH_ID, permissions=None):
    return AuthenticatedPrincipal(
        role=role,
        business_id=business_id,
        branch_id=None if role is Role.OWNER else branch_id,
        permissions=(
            ROLE_PERMISSIONS[role] if permissions is None else frozenset(permissions)
        ),
    )


def category(*, category_id=CATEGORY_ID, business_id=BUSINESS_ID, name="Beverages", active=True):
    return CategoryContext(
        category_id=category_id,
        name=name,
        scope=ResourceScope(business_id, None),
        is_active=active,
    )


def product_data(*, category_record=None, **changes):
    category_record = category_record or category()
    fields = {
        "business_id": BUSINESS_ID,
        "name": "Green Tea",
        "product_code": "TEA-01",
        "category_id": category_record.category_id,
        "selling_price": "2.500",
        "cost_price": "1.250",
        "category": ProductCategoryContext(
            category_record.category_id,
            category_record.scope,
            category_record.is_active,
        ),
    }
    fields.update(changes)
    return validate_product_data(**fields)


def product(*, product_id=PRODUCT_ID, business_id=BUSINESS_ID):
    return ProductContext(
        product_id=product_id,
        scope=ResourceScope(business_id, None),
    )


def variant(
    variant_id=DEFAULT_VARIANT_ID,
    *,
    product_id=PRODUCT_ID,
    business_id=BUSINESS_ID,
    code="TEA-01-DEFAULT",
    size=None,
    color=None,
    selling_price=None,
    cost_price=None,
    is_default=True,
):
    context = ProductVariantContext(
        variant_id=variant_id,
        product_id=product_id,
        scope=ResourceScope(business_id, None),
        is_default=is_default,
    )
    data = VariantData(
        product_id=product_id,
        code=code,
        size=size,
        color=color,
        selling_price=selling_price,
        cost_price=cost_price,
        is_default=is_default,
    )
    return ProductOverviewVariantRecord(context, data)


def branch(branch_id=BRANCH_ID, *, business_id=BUSINESS_ID, name=None):
    return BranchOverviewContext(
        branch_id=branch_id,
        name=name or f"Branch {branch_id}",
        scope=ResourceScope(business_id, branch_id),
    )


def balance(
    branch_id=BRANCH_ID,
    variant_id=DEFAULT_VARIANT_ID,
    *,
    available=8,
    minimum=2,
    inspection=3,
    damaged=4,
    in_transit=9,
):
    return {
        "branch_id": branch_id,
        "product_variant_id": variant_id,
        "available_qty": available,
        "minimum_stock_level": minimum,
        "inspection_qty": inspection,
        "damaged_qty": damaged,
        "in_transit_qty": in_transit,
    }


def movement(
    *,
    branch_id=BRANCH_ID,
    variant_id=DEFAULT_VARIANT_ID,
    business_id=BUSINESS_ID,
    quantity=-1,
    source_reference="sale-1",
    actor_id=501,
):
    return ProposedInventoryMovement(
        business_id=business_id,
        branch_id=branch_id,
        product_variant_id=variant_id,
        movement_type=MovementType.SALE,
        quantity_delta=quantity,
        source_type=MovementSource.SALE,
        source_reference=source_reference,
        origin=MovementOrigin.ACTOR,
        actor_id=actor_id,
    )


def build(**changes):
    args = {
        "principal": principal(),
        "product_id": PRODUCT_ID,
        "product": product(),
        "product_data": product_data(),
        "category": category(),
        "variants": [variant()],
        "branches": [branch()],
    }
    args.update(changes)
    return build_product_overview(**args)


def test_valid_product_details_include_category_prices_and_cost_for_owner():
    result = build(
        product_data=product_data(name="  Green Tea ", product_code=" TEA-01 "),
        category=category(name="  Beverages "),
    )

    assert result.product_id == PRODUCT_ID
    assert result.name == "Green Tea"
    assert result.product_code == "TEA-01"
    assert (result.category_id, result.category_name) == (CATEGORY_ID, "Beverages")
    assert result.selling_price == Decimal("2.500")
    assert result.cost_price == Decimal("1.250")
    assert result.variants[0].code == "TEA-01-DEFAULT"
    assert result.variants[0].cost_price == Decimal("1.250")


def test_multiple_variants_show_codes_size_color_and_price_overrides():
    second = variant(
        SECOND_VARIANT_ID,
        code="TEA-L-GREEN",
        size="Large",
        color="Green",
        selling_price=Decimal("3.000"),
        cost_price=Decimal("1.500"),
        is_default=False,
    )
    result = build(variants=[variant(), second])

    assert [item.code for item in result.variants] == [
        "TEA-01-DEFAULT",
        "TEA-L-GREEN",
    ]
    assert result.variants[0].selling_price == Decimal("2.500")
    assert result.variants[1].selling_price == Decimal("3.000")
    assert result.variants[1].size == "Large"
    assert result.variants[1].color == "Green"
    assert result.variants[1].cost_price == Decimal("1.500")


def test_available_stock_excludes_inspection_damaged_and_in_transit_quantities():
    result = build(inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): balance()})
    row = result.variants[0].branch_stock[0]

    assert row.available_qty == 8
    assert row.inspection_qty == 3
    assert row.damaged_qty == 4
    assert row.in_transit_qty == 9
    assert result.total_available_qty == 8


def test_owner_can_read_stock_summaries_for_multiple_branches():
    records = {
        (BRANCH_ID, DEFAULT_VARIANT_ID): balance(available=5, minimum=4),
        (OTHER_BRANCH_ID, DEFAULT_VARIANT_ID): balance(
            OTHER_BRANCH_ID, available=12, minimum=2
        ),
    }
    result = build(
        branches=[branch(), branch(OTHER_BRANCH_ID)],
        inventory_records=records,
    )

    assert [(row.branch_id, row.available_qty) for row in result.branch_stock] == [
        (BRANCH_ID, 5),
        (OTHER_BRANCH_ID, 12),
    ]
    assert result.total_available_qty == 17


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.EMPLOYEE])
def test_branch_scoped_roles_only_receive_their_authorized_branch(role):
    result = build(
        principal=principal(role),
        branches=[branch(), branch(OTHER_BRANCH_ID)],
        inventory_records={
            (BRANCH_ID, DEFAULT_VARIANT_ID): balance(available=4),
            (OTHER_BRANCH_ID, DEFAULT_VARIANT_ID): balance(
                OTHER_BRANCH_ID, available=99
            ),
        },
    )

    assert [row.branch_id for row in result.branch_stock] == [BRANCH_ID]
    assert result.total_available_qty == 4
    assert [row.branch_id for row in result.variants[0].branch_stock] == [BRANCH_ID]


def test_employee_and_branch_manager_cost_prices_follow_existing_permissions():
    for role in (Role.BRANCH_MANAGER, Role.EMPLOYEE):
        result = build(principal=principal(role))
        assert result.selling_price == Decimal("2.500")
        assert result.cost_price is None
        assert result.variants[0].cost_price is None


def test_narrowed_owner_grants_hide_stock_or_cost_fields_independently():
    result = build(
        principal=principal(permissions={PRODUCTS_VIEW}),
        inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): balance()},
    )

    assert result.cost_price is None
    assert result.variants[0].cost_price is None
    assert result.branch_stock == ()
    assert result.variants[0].branch_stock == ()


def test_branch_roles_need_products_view_on_an_authorized_branch():
    restricted = principal(Role.BRANCH_MANAGER, permissions={INVENTORY_VIEW})
    with pytest.raises(PermissionError):
        build(principal=restricted)


def test_foreign_business_product_is_denied():
    with pytest.raises(PermissionError):
        build(product=product(business_id=99))


def test_foreign_business_category_and_variant_are_denied():
    with pytest.raises(PermissionError):
        build(category=category(business_id=99))
    with pytest.raises(PermissionError):
        build(variants=[variant(business_id=99)])


def test_variant_must_belong_to_the_requested_product():
    with pytest.raises(PermissionError):
        build(variants=[variant(product_id=PRODUCT_ID + 1)])


def test_foreign_branch_and_foreign_stock_rows_are_not_returned():
    result = build(
        branches=[branch(), branch(OTHER_BRANCH_ID, business_id=99)],
        inventory_records={
            (BRANCH_ID, DEFAULT_VARIANT_ID): balance(available=6),
            (OTHER_BRANCH_ID, DEFAULT_VARIANT_ID): balance(
                OTHER_BRANCH_ID, available=500
            ),
        },
    )

    assert [row.branch_id for row in result.branch_stock] == [BRANCH_ID]
    assert result.total_available_qty == 6


def test_inventory_row_claiming_another_business_is_denied():
    foreign = balance()
    foreign["business_id"] = 99

    with pytest.raises(PermissionError):
        build(inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): foreign})


def test_missing_inventory_record_is_zero_and_out_of_stock():
    result = build(inventory_records={})

    assert result.variants[0].branch_stock[0].available_qty == 0
    assert result.variants[0].branch_stock[0].status.value == "OUT_OF_STOCK"
    assert result.branch_stock[0].status.value == "OUT_OF_STOCK"


def test_minimum_stock_and_low_stock_classification_are_branch_variant_specific():
    result = build(
        variants=[
            variant(),
            variant(
                SECOND_VARIANT_ID,
                code="TEA-L",
                size="Large",
                is_default=False,
            ),
        ],
        inventory_records={
            (BRANCH_ID, DEFAULT_VARIANT_ID): balance(available=4, minimum=4),
            (BRANCH_ID, SECOND_VARIANT_ID): balance(
                variant_id=SECOND_VARIANT_ID, available=5, minimum=3
            ),
        },
    )

    assert [row.minimum_stock_level for row in result.variants[0].branch_stock] == [4]
    assert result.variants[0].branch_stock[0].status.value == "LOW_STOCK"
    assert result.variants[1].branch_stock[0].status.value == "IN_STOCK"
    assert result.branch_stock[0].minimum_stock_level == 7
    assert result.branch_stock[0].status.value == "IN_STOCK"


def test_recent_movements_are_filtered_sorted_and_limited_using_trusted_times():
    newer = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    older = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    result = build(
        movement_records=[
            InventoryMovementHistoryRecord(
                movement(source_reference="old"), older, "Owner-visible old actor"
            ),
            InventoryMovementHistoryRecord(
                movement(source_reference="new"), newer, "Owner-visible actor"
            ),
            InventoryMovementHistoryRecord(
                movement(branch_id=OTHER_BRANCH_ID, source_reference="other-branch"),
                newer,
                "Other actor",
            ),
        ],
        movement_since=datetime(2026, 11, 4, tzinfo=timezone.utc),
        movement_limit=5,
    )

    assert len(result.recent_movements) == 1
    item = result.recent_movements[0]
    assert item.movement_type is MovementType.SALE
    assert item.signed_quantity == -1
    assert item.branch_id == BRANCH_ID
    assert item.occurred_at == newer
    assert item.responsible_user.user_id == 501
    assert item.responsible_user.name == "Owner-visible actor"


def test_recent_movement_ordering_is_descending_and_limit_is_applied():
    times = [
        datetime(2026, 11, day, tzinfo=timezone.utc)
        for day in (1, 2, 3)
    ]
    result = build(
        movement_records=[
            InventoryMovementHistoryRecord(
                movement(source_reference=f"sale-{index}"), timestamp
            )
            for index, timestamp in enumerate(times)
        ],
        movement_limit=2,
    )

    assert [item.occurred_at for item in result.recent_movements] == list(
        reversed(times[1:])
    )


def test_responsible_user_is_hidden_for_roles_without_user_management_permission():
    when = datetime(2026, 11, 5, tzinfo=timezone.utc)
    result = build(
        principal=principal(Role.EMPLOYEE),
        movement_records=[
            InventoryMovementHistoryRecord(
                movement(), when, "Private name"
            )
        ],
    )

    assert result.recent_movements[0].responsible_user is None


def test_foreign_business_and_unauthorized_branch_movements_are_omitted():
    when = datetime(2026, 11, 5, tzinfo=timezone.utc)
    result = build(
        principal=principal(Role.BRANCH_MANAGER),
        branches=[branch(), branch(OTHER_BRANCH_ID)],
        movement_records=[
            InventoryMovementHistoryRecord(
                movement(business_id=99, source_reference="foreign"), when
            ),
            InventoryMovementHistoryRecord(
                movement(branch_id=OTHER_BRANCH_ID, source_reference="other-branch"),
                when,
            ),
        ],
    )

    assert result.recent_movements == ()


@pytest.mark.parametrize("bad_id", [True, 0, -1, "50", None])
def test_invalid_product_ids_are_rejected(bad_id):
    with pytest.raises(ValueError):
        build(product_id=bad_id)


def test_untrusted_principal_and_mismatched_trusted_product_are_rejected():
    with pytest.raises(PermissionError):
        build(principal={"role": "Owner", "business_id": BUSINESS_ID})
    with pytest.raises(ValueError):
        build(product=product(product_id=PRODUCT_ID + 1))


def test_product_must_have_exactly_one_default_variant():
    with pytest.raises(ValueError, match="exactly one default"):
        build(
            variants=[
                variant(),
                variant(
                    SECOND_VARIANT_ID,
                    code="TEA-L",
                    size="Large",
                    is_default=False,
                ),
                variant(103, code="TEA-OTHER"),
            ]
        )


@pytest.mark.parametrize(
    "bad_balance",
    [
        {"branch_id": BRANCH_ID, "product_variant_id": DEFAULT_VARIANT_ID},
        balance(available=True),
        balance(available=-1),
        balance(minimum=True),
        balance(inspection=-1),
        balance(damaged=1.5),
        balance(in_transit="9"),
        balance(branch_id=OTHER_BRANCH_ID),
    ],
)
def test_malformed_or_mismatched_existing_inventory_is_rejected(bad_balance):
    with pytest.raises(ValueError):
        build(inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): bad_balance})


def test_legacy_under_inspection_field_is_kept_separate():
    record = balance()
    del record["inspection_qty"]
    record["under_inspection_qty"] = 6
    result = build(inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): record})

    assert result.variants[0].branch_stock[0].inspection_qty == 6
    assert result.variants[0].branch_stock[0].available_qty == 8


def test_conflicting_inspection_fields_are_rejected():
    record = balance()
    record["under_inspection_qty"] = 8
    with pytest.raises(ValueError, match="conflicting"):
        build(inventory_records={(BRANCH_ID, DEFAULT_VARIANT_ID): record})


def test_missing_or_naive_movement_timestamps_are_never_invented():
    with pytest.raises(ValueError, match="timezone-aware"):
        build(
            movement_records=[
                InventoryMovementHistoryRecord(movement(), None)
            ]
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        build(
            movement_records=[
                InventoryMovementHistoryRecord(
                    movement(), datetime(2026, 11, 5)
                )
            ]
        )


def test_malformed_movement_effect_is_rejected():
    bad = replace(movement(), quantity_delta=1)
    with pytest.raises(ValueError):
        build(
            movement_records=[
                InventoryMovementHistoryRecord(
                    bad, datetime(2026, 11, 5, tzinfo=timezone.utc)
                )
            ]
        )


def test_invalid_movement_window_and_limit_are_rejected():
    with pytest.raises(ValueError, match="positive integer"):
        build(movement_limit=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        build(movement_since=datetime(2026, 11, 1))

