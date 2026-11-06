"""Read-only business rules for product details and branch stock overviews.

The caller supplies product, variant, branch, inventory, and movement records
loaded by trusted server code. This module authorizes and shapes those records
for display; it never queries a database, changes stock, or creates movements.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from category_rules import CategoryContext
from inventory_movement_rules import (
    MovementType,
    ProposedInventoryMovement,
    _validate_movement,
)
from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    can_access_resource,
    filter_accessible_resources,
    require_resource_access,
    resources_belong_to_business,
)
from permissions import (
    INVENTORY_VIEW,
    PRODUCT_CATALOG_MANAGE,
    PRODUCTS_VIEW,
    USERS_MANAGE,
    Role,
    ResourceScope,
    is_trusted_principal,
    is_valid_resource_scope,
)
from product_rules import (
    PRODUCT_CODE_MAX_LENGTH,
    ProductCategoryContext,
    ProductData,
    _normalize_money,
    validate_product_data,
)
from request_validation import is_positive_integer
from stock_rules import (
    StockStatus,
    classify_stock_status,
    get_available_quantity,
    validate_non_negative_integer,
)
from variant_rules import ProductContext, ProductVariantContext, VariantData


@dataclass(frozen=True)
class BranchOverviewContext:
    """A server-loaded branch and its trusted resource scope."""

    branch_id: int
    name: str
    scope: ResourceScope


@dataclass(frozen=True)
class ProductOverviewVariantRecord:
    """A server-loaded variant context and its validated catalog attributes."""

    context: ProductVariantContext
    data: VariantData


@dataclass(frozen=True)
class BranchInventoryRecord:
    """Trusted branch-variant inventory fields used by the overview."""

    branch_id: int
    product_variant_id: int
    available_qty: int
    minimum_stock_level: int = 0
    inspection_qty: int = 0
    damaged_qty: int = 0
    in_transit_qty: int = 0
    business_id: int | None = None


@dataclass(frozen=True)
class InventoryMovementHistoryRecord:
    """A persisted movement effect with its trusted ordering timestamp."""

    movement: ProposedInventoryMovement
    occurred_at: datetime
    responsible_user_name: str | None = None


@dataclass(frozen=True)
class VariantBranchStock:
    """Stock quantities for one product variant at one authorized branch."""

    branch_id: int
    branch_name: str
    available_qty: int
    inspection_qty: int
    damaged_qty: int
    in_transit_qty: int
    minimum_stock_level: int
    status: StockStatus


@dataclass(frozen=True)
class ProductVariantOverview:
    """Displayable catalog attributes and branch stock for one variant."""

    product_variant_id: int
    code: str
    size: str | None
    color: str | None
    is_default: bool
    selling_price: Decimal
    cost_price: Decimal | None
    branch_stock: tuple[VariantBranchStock, ...]


@dataclass(frozen=True)
class ProductBranchStockSummary:
    """Product stock totals for one authorized branch."""

    branch_id: int
    branch_name: str
    available_qty: int
    inspection_qty: int
    damaged_qty: int
    in_transit_qty: int
    minimum_stock_level: int
    status: StockStatus


@dataclass(frozen=True)
class ResponsibleUser:
    """Responsible actor details visible under the existing user grant."""

    user_id: int
    name: str | None


@dataclass(frozen=True)
class RecentInventoryMovement:
    """A recent movement row safe to include in the authorized overview."""

    movement_type: MovementType
    signed_quantity: int
    branch_id: int
    branch_name: str
    occurred_at: datetime
    responsible_user: ResponsibleUser | None


@dataclass(frozen=True)
class ProductOverview:
    """Read-only product, variant, stock, and recent movement presentation."""

    product_id: int
    name: str
    product_code: str
    category_id: int
    category_name: str
    selling_price: Decimal
    cost_price: Decimal | None
    variants: tuple[ProductVariantOverview, ...]
    branch_stock: tuple[ProductBranchStockSummary, ...]
    recent_movements: tuple[RecentInventoryMovement, ...]

    @property
    def total_available_qty(self):
        """Total sellable stock across the authorized branches in this result."""
        return sum(branch.available_qty for branch in self.branch_stock)


_MISSING = object()


def _iter_records(value, field_name):
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ValueError(f"{field_name} must be an iterable of trusted records.")
    return tuple(value)


def _record_field(record, field_names, *, default=_MISSING):
    """Read one trusted field, optionally accepting the legacy inspection name."""
    found = []
    for field_name in field_names:
        if isinstance(record, Mapping):
            if field_name in record:
                found.append(record[field_name])
        else:
            value = getattr(record, field_name, _MISSING)
            if value is not _MISSING:
                found.append(value)
    if len(found) > 1 and any(value != found[0] for value in found[1:]):
        raise ValueError(f"Inventory record has conflicting {field_names[0]} values.")
    if found:
        return found[0]
    if default is not _MISSING:
        return default
    raise ValueError(f"Inventory record is missing {field_names[0]}.")


def _validate_product(product_id, product, product_data, category):
    if not is_positive_integer(product_id):
        raise ValueError("product_id must be a positive integer.")
    if type(product) is not ProductContext:
        raise ValueError("A trusted product context is required.")
    if not is_positive_integer(product.product_id) or product.product_id != product_id:
        raise ValueError("The trusted product does not match product_id.")
    if not is_valid_resource_scope(product.scope) or product.scope.branch_id is not None:
        raise ValueError("A trusted business-wide product scope is required.")

    if type(category) is not CategoryContext:
        raise ValueError("A trusted category context is required.")
    if not is_positive_integer(category.category_id):
        raise ValueError("The trusted category has an invalid category_id.")
    if (
        not is_valid_resource_scope(category.scope)
        or category.scope.branch_id is not None
    ):
        raise ValueError("A trusted business-wide category scope is required.")
    if type(category.is_active) is not bool:
        raise ValueError("The trusted category has an invalid is_active value.")
    if not isinstance(category.name, str) or not category.name.strip():
        raise ValueError("The trusted category has an invalid name.")

    if not resources_belong_to_business(
        product.scope.business_id, category.scope
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if type(product_data) is not ProductData:
        raise ValueError("Validated ProductData is required.")

    # Reuse the product rules to verify stored names, codes, category linkage,
    # and exact money values. An inactive category remains displayable because
    # deactivation preserves existing product references.
    return validate_product_data(
        business_id=product.scope.business_id,
        name=product_data.name,
        product_code=product_data.product_code,
        category_id=product_data.category_id,
        selling_price=product_data.selling_price,
        cost_price=product_data.cost_price,
        category=ProductCategoryContext(
            category_id=category.category_id,
            scope=category.scope,
            is_active=category.is_active,
        ),
        _require_active_category=False,
    )


def _validate_variant_records(variants, product):
    records = _iter_records(variants, "variants")
    validated = []
    seen_ids = set()
    default_count = 0
    for record in records:
        if type(record) is not ProductOverviewVariantRecord:
            raise ValueError(
                "variants must contain trusted ProductOverviewVariantRecord values."
            )
        context = record.context
        data = record.data
        if type(context) is not ProductVariantContext or type(data) is not VariantData:
            raise ValueError("A trusted variant context and VariantData are required.")
        if not is_positive_integer(context.variant_id):
            raise ValueError("A trusted variant has an invalid product_variant_id.")
        if context.variant_id in seen_ids:
            raise ValueError("variants contains a duplicate product_variant_id.")
        seen_ids.add(context.variant_id)
        if (
            not is_positive_integer(context.product_id)
            or not is_positive_integer(data.product_id)
            or context.product_id != product.product_id
            or data.product_id != product.product_id
        ):
            raise PermissionError(ACCESS_DENIED_MESSAGE)
        if (
            not is_valid_resource_scope(context.scope)
            or context.scope.branch_id is not None
        ):
            raise ValueError("A trusted business-wide variant scope is required.")
        if not resources_belong_to_business(product.scope.business_id, context.scope):
            raise PermissionError(ACCESS_DENIED_MESSAGE)
        if type(context.is_default) is not bool or type(data.is_default) is not bool:
            raise ValueError("The trusted variant has an invalid is_default value.")
        if context.is_default != data.is_default:
            raise ValueError("Variant default metadata does not match its context.")
        if (
            not isinstance(data.code, str)
            or not data.code.strip()
            or len(data.code.strip()) > PRODUCT_CODE_MAX_LENGTH
        ):
            raise ValueError("The trusted variant has an invalid code.")
        for attribute_name in ("size", "color"):
            attribute = getattr(data, attribute_name)
            if attribute is not None and (
                not isinstance(attribute, str) or not attribute.strip()
            ):
                raise ValueError(f"The trusted variant has an invalid {attribute_name}.")
        if data.is_default:
            default_count += 1
            if data.size is not None or data.color is not None:
                raise ValueError("A default variant cannot have size or color.")
        elif data.size is None and data.color is None:
            raise ValueError("A non-default variant requires size or color.")

        # ``variant_rules`` normalizes optional price overrides through this
        # shared product money validator. Keep stored override validation aligned.
        selling_price = (
            None
            if data.selling_price is None
            else _normalize_money(data.selling_price, "selling_price")
        )
        cost_price = (
            None if data.cost_price is None else _normalize_money(data.cost_price, "cost_price")
        )
        validated.append((context, data, selling_price, cost_price))

    if not validated:
        raise ValueError("A product must have at least one trusted variant.")
    if default_count != 1:
        raise ValueError("A product must have exactly one default variant.")
    return tuple(validated)


def _validate_branch_records(branches, business_id):
    records = _iter_records(branches, "branches")
    seen_ids = set()
    for branch in records:
        if type(branch) is not BranchOverviewContext:
            raise ValueError("branches must contain trusted BranchOverviewContext values.")
        if not is_positive_integer(branch.branch_id):
            raise ValueError("A trusted branch has an invalid branch_id.")
        if branch.branch_id in seen_ids:
            raise ValueError("branches contains a duplicate branch_id.")
        seen_ids.add(branch.branch_id)
        if not isinstance(branch.name, str) or not branch.name.strip():
            raise ValueError("A trusted branch has an invalid name.")
        if (
            not is_valid_resource_scope(branch.scope)
            or branch.scope.branch_id != branch.branch_id
        ):
            raise ValueError("A trusted branch scope does not match branch_id.")
        if branch.scope.business_id != business_id:
            # Silently omit foreign branches to preserve collection isolation.
            continue
    return records


def _authorized_branches(principal, branches, product, variants):
    variant_scopes = tuple(context.scope for context, *_ in variants)
    related_scopes = (product.scope, *variant_scopes)
    visible = filter_accessible_resources(
        principal,
        PRODUCTS_VIEW,
        branches,
        scope_getter=lambda branch: branch.scope,
        related_scopes_getter=lambda _branch: related_scopes,
    )

    if not visible and principal.role is not Role.OWNER:
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    if principal.role is Role.OWNER:
        require_resource_access(principal, PRODUCTS_VIEW, product.scope)

    return filter_accessible_resources(
        principal,
        INVENTORY_VIEW,
        visible,
        scope_getter=lambda branch: branch.scope,
        related_scopes_getter=lambda _branch: related_scopes,
    )


def _read_inventory_quantities(record, branch_id, variant_id, business_id):
    available_qty = get_available_quantity(record, branch_id, variant_id)
    if record is None:
        return available_qty, 0, 0, 0, 0

    record_business_id = _record_field(record, ("business_id",), default=None)
    if record_business_id is not None:
        if not is_positive_integer(record_business_id):
            raise ValueError("Inventory record has an invalid business_id.")
        if record_business_id != business_id:
            raise PermissionError(ACCESS_DENIED_MESSAGE)
    record_branch_id = _record_field(record, ("branch_id",))
    record_variant_id = _record_field(record, ("product_variant_id",))
    if (record_branch_id, record_variant_id) != (branch_id, variant_id):
        raise ValueError("Inventory record does not match the requested branch and variant.")
    minimum = _record_field(record, ("minimum_stock_level",), default=0)
    inspection = _record_field(
        record, ("inspection_qty", "under_inspection_qty"), default=0
    )
    damaged = _record_field(record, ("damaged_qty",), default=0)
    in_transit = _record_field(record, ("in_transit_qty",), default=0)
    minimum = validate_non_negative_integer(minimum, "minimum_stock_level")
    inspection = validate_non_negative_integer(inspection, "inspection_qty")
    damaged = validate_non_negative_integer(damaged, "damaged_qty")
    in_transit = validate_non_negative_integer(in_transit, "in_transit_qty")
    return available_qty, minimum, inspection, damaged, in_transit


def _validate_inventory_records(inventory_records):
    if inventory_records is None:
        return {}
    if not isinstance(inventory_records, Mapping):
        raise ValueError("inventory_records must map (branch_id, variant_id) to records.")
    for key in inventory_records:
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("Inventory record keys must be (branch_id, product_variant_id).")
        if not is_positive_integer(key[0]) or not is_positive_integer(key[1]):
            raise ValueError("Inventory record keys must contain positive integer IDs.")
    return inventory_records


def _validate_aware_timestamp(value, field_name):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware trusted timestamp.")
    return value


def _recent_movements(
    principal,
    movement_records,
    branches_by_id,
    variants_by_id,
    product_scope,
    *,
    movement_since,
    movement_limit,
):
    if type(movement_limit) is not int or movement_limit <= 0:
        raise ValueError("movement_limit must be a positive integer.")
    if movement_since is not None:
        movement_since = _validate_aware_timestamp(movement_since, "movement_since")
    records = _iter_records(movement_records, "movement_records")
    visible = []
    seen_effects = set()
    for record in records:
        if type(record) is not InventoryMovementHistoryRecord:
            raise ValueError(
                "movement_records must contain trusted InventoryMovementHistoryRecord values."
            )
        occurred_at = _validate_aware_timestamp(record.occurred_at, "occurred_at")
        movement = record.movement
        if type(movement) is not ProposedInventoryMovement:
            raise ValueError("A trusted ProposedInventoryMovement is required.")
        if (
            not is_positive_integer(movement.business_id)
            or not is_positive_integer(movement.branch_id)
            or not is_positive_integer(movement.product_variant_id)
        ):
            raise ValueError("Movement business, branch, and variant IDs must be positive.")
        if movement.business_id != product_scope.business_id:
            continue
        branch = branches_by_id.get(movement.branch_id)
        variant = variants_by_id.get(movement.product_variant_id)
        if branch is None or variant is None:
            continue
        if not resources_belong_to_business(
            product_scope.business_id, branch.scope, variant.scope
        ):
            continue

        # Reuse the inventory movement module's structural validator, without
        # planning a balance change or manufacturing stock for historical rows.
        _validate_movement(movement)
        effect_reference = movement.effect_reference
        if effect_reference in seen_effects:
            raise ValueError("Movement history contains a duplicate movement effect.")
        seen_effects.add(effect_reference)
        if movement_since is not None and occurred_at < movement_since:
            continue

        responsible_user = None
        if movement.actor_id is not None and can_access_resource(
            principal, USERS_MANAGE, branch.scope
        ):
            if record.responsible_user_name is not None and (
                not isinstance(record.responsible_user_name, str)
                or not record.responsible_user_name.strip()
            ):
                raise ValueError("responsible_user_name must be non-empty text when supplied.")
            responsible_user = ResponsibleUser(
                user_id=movement.actor_id,
                name=(
                    None
                    if record.responsible_user_name is None
                    else record.responsible_user_name.strip()
                ),
            )
        elif record.responsible_user_name is not None and (
            not isinstance(record.responsible_user_name, str)
            or not record.responsible_user_name.strip()
        ):
            raise ValueError("responsible_user_name must be non-empty text when supplied.")

        visible.append(
            RecentInventoryMovement(
                movement_type=movement.movement_type,
                signed_quantity=movement.quantity_delta,
                branch_id=branch.branch_id,
                branch_name=branch.name.strip(),
                occurred_at=occurred_at,
                responsible_user=responsible_user,
            )
        )

    visible.sort(key=lambda item: item.occurred_at, reverse=True)
    return tuple(visible[:movement_limit])


def build_product_overview(
    principal,
    *,
    product_id,
    product,
    product_data,
    category,
    variants,
    branches=(),
    inventory_records=None,
    movement_records=(),
    movement_since=None,
    movement_limit=10,
):
    """Authorize and build a read-only product overview from trusted records.

    ``branches`` is the server-loaded branch selection for this read. Owners
    may select any supplied branch in their business; Branch Managers and
    Employees only receive rows for their authorized branch. Missing balance
    rows are zeroed by the existing stock rules. Cost prices and responsible
    user details are included only where the existing management grants allow.
    """
    if not is_trusted_principal(principal):
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    normalized_product = _validate_product(product_id, product, product_data, category)
    if product.scope.business_id != principal.business_id:
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if category.category_id != normalized_product.category_id:
        raise ValueError("The trusted category does not match the product category_id.")
    variants = _validate_variant_records(variants, product)
    if not resources_belong_to_business(
        principal.business_id,
        product.scope,
        category.scope,
        *(context.scope for context, *_ in variants),
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    branches = _validate_branch_records(branches, principal.business_id)
    stock_branches = _authorized_branches(principal, branches, product, variants)

    # The shared catalog is readable by an owner at business scope. For a
    # branch role, _authorized_branches already required products:view on a
    # server-loaded branch and related same-business product/variant scopes.
    can_view_cost = can_access_resource(
        principal, PRODUCT_CATALOG_MANAGE, product.scope
    )
    normalized_inventory = _validate_inventory_records(inventory_records)
    variant_outputs = []
    stock_by_branch = {branch.branch_id: [] for branch in stock_branches}

    for context, data, variant_selling, variant_cost in variants:
        variant_stock = []
        for branch in stock_branches:
            record = normalized_inventory.get((branch.branch_id, context.variant_id))
            available, minimum, inspection, damaged, in_transit = _read_inventory_quantities(
                record,
                branch.branch_id,
                context.variant_id,
                product.scope.business_id,
            )
            status = classify_stock_status(available, minimum)
            row = VariantBranchStock(
                branch_id=branch.branch_id,
                branch_name=branch.name.strip(),
                available_qty=available,
                inspection_qty=inspection,
                damaged_qty=damaged,
                in_transit_qty=in_transit,
                minimum_stock_level=minimum,
                status=status,
            )
            variant_stock.append(row)
            stock_by_branch[branch.branch_id].append(row)

        variant_outputs.append(
            ProductVariantOverview(
                product_variant_id=context.variant_id,
                code=data.code.strip(),
                size=None if data.size is None else data.size.strip(),
                color=None if data.color is None else data.color.strip(),
                is_default=data.is_default,
                selling_price=(
                    normalized_product.selling_price
                    if variant_selling is None
                    else variant_selling
                ),
                cost_price=(
                    None
                    if not can_view_cost
                    else normalized_product.cost_price
                    if variant_cost is None
                    else variant_cost
                ),
                branch_stock=tuple(variant_stock),
            )
        )

    branch_summaries = []
    for branch in stock_branches:
        rows = stock_by_branch[branch.branch_id]
        available = sum(item.available_qty for item in rows)
        minimum = sum(item.minimum_stock_level for item in rows)
        branch_summaries.append(
            ProductBranchStockSummary(
                branch_id=branch.branch_id,
                branch_name=branch.name.strip(),
                available_qty=available,
                inspection_qty=sum(item.inspection_qty for item in rows),
                damaged_qty=sum(item.damaged_qty for item in rows),
                in_transit_qty=sum(item.in_transit_qty for item in rows),
                minimum_stock_level=minimum,
                status=classify_stock_status(available, minimum),
            )
        )

    branches_by_id = {branch.branch_id: branch for branch in stock_branches}
    variants_by_id = {context.variant_id: context for context, *_ in variants}
    recent = _recent_movements(
        principal,
        movement_records,
        branches_by_id,
        variants_by_id,
        product.scope,
        movement_since=movement_since,
        movement_limit=movement_limit,
    )

    return ProductOverview(
        product_id=product.product_id,
        name=normalized_product.name,
        product_code=normalized_product.product_code,
        category_id=normalized_product.category_id,
        category_name=category.name.strip(),
        selling_price=normalized_product.selling_price,
        cost_price=(normalized_product.cost_price if can_view_cost else None),
        variants=tuple(variant_outputs),
        branch_stock=tuple(branch_summaries),
        recent_movements=recent,
    )


__all__ = [
    "BranchInventoryRecord",
    "BranchOverviewContext",
    "InventoryMovementHistoryRecord",
    "ProductBranchStockSummary",
    "ProductOverview",
    "ProductOverviewVariantRecord",
    "ProductVariantOverview",
    "RecentInventoryMovement",
    "ResponsibleUser",
    "VariantBranchStock",
    "build_product_overview",
]
