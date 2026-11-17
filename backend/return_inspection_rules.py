"""Pure business rules for classifying returned products after inspection.

The caller must supply return items and inspection history loaded from trusted
server-side data. These functions do not persist inspections or change stock.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from permissions import AuthenticatedPrincipal, Role
from return_rules import (
    ReturnRequest,
    validate_return_inspection as _validate_return_inspection,
    validate_return_items,
)


class InspectionCondition(str, Enum):
    """Inspection outcome for returned products."""

    UNDER_INSPECTION = "UNDER_INSPECTION"
    RESALABLE = "RESALABLE"
    DAMAGED = "DAMAGED"


@dataclass(frozen=True)
class ReturnedItem:
    """Trusted return line loaded by the caller."""

    return_item_id: int
    product_variant_id: int
    quantity: int


@dataclass(frozen=True)
class ReturnInspectionItem:
    """Quantity from one return line assigned an inspection condition."""

    return_item_id: int
    quantity: int
    condition: InspectionCondition


@dataclass(frozen=True)
class ReturnInspection:
    """Inspection record shape used as trusted in-memory history."""

    inspection_id: int
    items: tuple[ReturnInspectionItem, ...]


@dataclass(frozen=True)
class InspectionInventoryChanges:
    """Pure stock plan; under-inspection quantities remain unavailable."""

    available_stock_additions: dict[int, int]
    damaged_stock_additions: dict[int, int]
    remaining_under_inspection: dict[int, int]


def _require_positive_integer(value, field_name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")


def _as_condition(condition):
    try:
        return (
            condition
            if isinstance(condition, InspectionCondition)
            else InspectionCondition(condition)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown return inspection condition.") from None


def _read_returned_item(item):
    if isinstance(item, ReturnedItem):
        return_item_id = item.return_item_id
        variant_id = item.product_variant_id
        quantity = item.quantity
    elif isinstance(item, Mapping):
        required = {"return_item_id", "product_variant_id", "quantity"}
        if not required.issubset(item):
            raise ValueError("Returned items require an ID, variant ID, and quantity.")
        return_item_id = item["return_item_id"]
        variant_id = item["product_variant_id"]
        quantity = item["quantity"]
    else:
        raise ValueError("Each returned item must be a ReturnedItem or mapping.")

    _require_positive_integer(return_item_id, "return_item_id")
    _require_positive_integer(variant_id, "product_variant_id")
    _require_positive_integer(quantity, "quantity")
    return ReturnedItem(return_item_id, variant_id, quantity)


def _read_inspection_item(item):
    if isinstance(item, ReturnInspectionItem):
        return_item_id = item.return_item_id
        quantity = item.quantity
        condition = item.condition
    elif isinstance(item, Mapping):
        required = {"return_item_id", "quantity", "condition"}
        if not required.issubset(item):
            raise ValueError("Inspection items require a return item ID, quantity, and condition.")
        return_item_id = item["return_item_id"]
        quantity = item["quantity"]
        condition = item["condition"]
    else:
        raise ValueError("Each inspection item must be a ReturnInspectionItem or mapping.")

    _require_positive_integer(return_item_id, "return_item_id")
    _require_positive_integer(quantity, "inspection quantity")
    return ReturnInspectionItem(return_item_id, quantity, _as_condition(condition))


def _read_inspection(inspection):
    if not isinstance(inspection, ReturnInspection):
        raise ValueError("A trusted return inspection is required.")
    _require_positive_integer(inspection.inspection_id, "inspection_id")
    if not isinstance(inspection.items, (list, tuple)) or not inspection.items:
        raise ValueError("An inspection must contain at least one item.")
    return ReturnInspection(
        inspection.inspection_id,
        tuple(_read_inspection_item(item) for item in inspection.items),
    )


def _read_returned_items(return_request, returned_items):
    if not isinstance(return_request, ReturnRequest):
        raise ValueError("A trusted return request is required.")
    if not isinstance(returned_items, (list, tuple)) or not returned_items:
        raise ValueError("Returned items must be a non-empty list or tuple.")

    items = tuple(_read_returned_item(item) for item in returned_items)
    item_ids = [item.return_item_id for item in items]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("Return item IDs must be unique.")

    # Reuse return_rules validation and ensure the loaded lines represent the
    # exact trusted return quantities before validating an inspection.
    request_items = validate_return_items(return_request.items)
    loaded_items = validate_return_items(
        [
            {"product_variant_id": item.product_variant_id, "quantity": item.quantity}
            for item in items
        ]
    )
    if _quantities_by_variant(request_items) != _quantities_by_variant(loaded_items):
        raise ValueError("Returned item lines do not match the return request.")
    return items


def _quantities_by_variant(items):
    quantities = {}
    for item in items:
        variant_id = item["product_variant_id"]
        quantities[variant_id] = quantities.get(variant_id, 0) + item["quantity"]
    return quantities


def _read_history(inspection_history, returned_items):
    if inspection_history is None:
        inspection_history = ()
    if not isinstance(inspection_history, (list, tuple)):
        raise ValueError("Inspection history must be a list or tuple.")

    known_items = {item.return_item_id: item for item in returned_items}
    history = []
    seen_inspection_ids = set()
    final_quantities_by_item = {}
    for raw_inspection in inspection_history:
        inspection = _read_inspection(raw_inspection)
        if inspection.inspection_id in seen_inspection_ids:
            raise ValueError("Inspection history contains a duplicate inspection ID.")
        seen_inspection_ids.add(inspection.inspection_id)

        inspection_quantities = {}
        for item in inspection.items:
            returned_item = known_items.get(item.return_item_id)
            if returned_item is None:
                raise ValueError("Inspection history contains an item not in this return.")
            inspection_quantities[item.return_item_id] = (
                inspection_quantities.get(item.return_item_id, 0) + item.quantity
            )

        # UNDER_INSPECTION is a pending state, so it does not consume the
        # quantity available for a later final classification.
        for return_item_id, quantity in inspection_quantities.items():
            final_quantity = sum(
                item.quantity
                for item in inspection.items
                if item.return_item_id == return_item_id
                and item.condition in {InspectionCondition.RESALABLE, InspectionCondition.DAMAGED}
            )
            already_final = final_quantities_by_item.get(return_item_id, 0)
            if already_final + final_quantity > known_items[return_item_id].quantity:
                raise ValueError("Cumulative inspected quantity cannot exceed the returned quantity.")
            if final_quantity + sum(
                item.quantity
                for item in inspection.items
                if item.return_item_id == return_item_id
                and item.condition is InspectionCondition.UNDER_INSPECTION
            ) > known_items[return_item_id].quantity - already_final:
                raise ValueError("Inspection quantities cannot exceed the remaining returned quantity.")
            final_quantities_by_item[return_item_id] = already_final + final_quantity
        history.append(inspection)

    return tuple(history), final_quantities_by_item


def _final_quantities_by_variant(history, returned_items):
    known_items = {item.return_item_id: item for item in returned_items}
    quantities = {}
    for inspection in history:
        for item in inspection.items:
            if item.condition not in {InspectionCondition.RESALABLE, InspectionCondition.DAMAGED}:
                continue
            variant_id = known_items[item.return_item_id].product_variant_id
            quantities[variant_id] = quantities.get(variant_id, 0) + item.quantity
    return quantities


def validate_return_inspection(
    principal,
    return_request,
    returned_items,
    inspection_id,
    inspection_items,
    *,
    inspection_history=None,
):
    """Authorize and validate a new partial inspection without changing stock.

    ``returned_items`` and ``inspection_history`` must be trusted data loaded
    for ``return_request``. Unlinked returns use the verification rules in
    ``return_rules`` and must already be verified. History is ordered oldest
    first and its inspection IDs must be unique.
    """
    if not isinstance(principal, AuthenticatedPrincipal) or principal.role not in {
        Role.OWNER,
        Role.BRANCH_MANAGER,
    }:
        raise PermissionError("Only an Owner or Branch Manager may inspect returns.")

    items = _read_returned_items(return_request, returned_items)
    _require_positive_integer(inspection_id, "inspection_id")
    if not isinstance(inspection_items, (list, tuple)) or not inspection_items:
        raise ValueError("An inspection must contain at least one item.")
    current = ReturnInspection(
        inspection_id,
        tuple(_read_inspection_item(item) for item in inspection_items),
    )
    history, previously_final_by_item = _read_history(inspection_history, items)
    if any(old.inspection_id == current.inspection_id for old in history):
        raise ValueError("This inspection has already been processed.")

    known_items = {item.return_item_id: item for item in items}
    current_by_item = {}
    for item in current.items:
        if item.return_item_id not in known_items:
            raise ValueError("Cannot inspect a return item that was not returned.")
        current_by_item[item.return_item_id] = current_by_item.get(item.return_item_id, 0) + item.quantity

    for return_item_id, quantity in current_by_item.items():
        remaining = known_items[return_item_id].quantity - previously_final_by_item.get(return_item_id, 0)
        if quantity > remaining:
            raise ValueError("Inspected quantity cannot exceed the remaining returned quantity.")

    current_by_variant = {}
    for item in current.items:
        variant_id = known_items[item.return_item_id].product_variant_id
        current_by_variant[variant_id] = current_by_variant.get(variant_id, 0) + item.quantity
    previous_by_variant = _final_quantities_by_variant(history, items)
    _validate_return_inspection(
        principal,
        return_request,
        [
            {"product_variant_id": variant_id, "quantity": quantity}
            for variant_id, quantity in current_by_variant.items()
        ],
        previously_inspected_items=[
            {"product_variant_id": variant_id, "quantity": quantity}
            for variant_id, quantity in previous_by_variant.items()
        ],
    )
    return current


def calculate_remaining_under_inspection_quantities(
    return_request, returned_items, inspection_history=None
):
    """Return still unclassified quantities by return item ID.

    Resalable and damaged quantities are final classifications. Pending
    UNDER_INSPECTION history does not consume the remaining amount.
    """
    items = _read_returned_items(return_request, returned_items)
    history, final_quantities = _read_history(inspection_history, items)
    del history
    return {
        item.return_item_id: item.quantity - final_quantities.get(item.return_item_id, 0)
        for item in items
        if item.quantity - final_quantities.get(item.return_item_id, 0) > 0
    }


def calculate_inspection_inventory_changes(
    principal,
    return_request,
    returned_items,
    inspection_id,
    inspection_items,
    *,
    inspection_history=None,
):
    """Return expected stock effects for a newly authorized inspection.

    Only RESALABLE quantities are added to available stock. DAMAGED quantities
    are reported separately. UNDER_INSPECTION quantities stay unavailable and
    create no stock addition. Reusing an inspection ID from trusted history is
    rejected, preventing this pure plan from repeating the same effects.
    """
    inspection = validate_return_inspection(
        principal,
        return_request,
        returned_items,
        inspection_id,
        inspection_items,
        inspection_history=inspection_history,
    )
    items = _read_returned_items(return_request, returned_items)
    known_items = {item.return_item_id: item for item in items}
    available = {}
    damaged = {}
    for item in inspection.items:
        variant_id = known_items[item.return_item_id].product_variant_id
        if item.condition is InspectionCondition.RESALABLE:
            available[variant_id] = available.get(variant_id, 0) + item.quantity
        elif item.condition is InspectionCondition.DAMAGED:
            damaged[variant_id] = damaged.get(variant_id, 0) + item.quantity

    history, previously_final = _read_history(inspection_history, items)
    del history
    remaining = {}
    for item in items:
        current_final = sum(
            line.quantity
            for line in inspection.items
            if line.return_item_id == item.return_item_id
            and line.condition in {InspectionCondition.RESALABLE, InspectionCondition.DAMAGED}
        )
        quantity = item.quantity - previously_final.get(item.return_item_id, 0) - current_final
        if quantity > 0:
            remaining[item.return_item_id] = quantity

    return InspectionInventoryChanges(
        available_stock_additions=available,
        damaged_stock_additions=damaged,
        remaining_under_inspection=remaining,
    )
