"""Pure planning and validation rules for available-stock movements.

Operation builders consume trusted principals, server-loaded resource scopes,
and trusted business records. They reuse the existing business rules and return
movement effects; :func:`plan_inventory_movements` then checks idempotency and
calculates cumulative expected available quantities without changing records.
``actor_id`` must come from the same verified server-side identity as the
principal; the current principal type does not itself carry a user ID.

All movement effects in this module apply only to ``available_qty``. In-transit,
under-inspection, and damaged quantities are not credited to available stock.
Database integration must atomically persist the source state and append-only
movement history while locking each affected branch-variant balance row.
It must lock affected rows in a stable branch/variant order, re-read balances,
and re-plan inside that transaction. A unique constraint on ``effect_reference``
must enforce idempotency; the balance update, source state transition, and
append-only movement inserts must commit or roll back together. Movement history
must never be edited or deleted.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum

from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    can_access_transfer,
    require_resource_access,
    resources_belong_to_business,
)
from permissions import (
    PURCHASES_MANAGE,
    PURCHASES_MANAGE_AUTHORIZED,
    RETURNS_INSPECT,
    SALE_CANCELLATION_APPROVAL,
    SALES_RECORD,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_DISPATCH,
    AuthenticatedPrincipal,
    Role,
    is_trusted_principal,
    is_valid_resource_scope,
)
from request_validation import is_positive_integer
from return_inspection_rules import (
    ReturnedItem,
    calculate_inspection_inventory_changes,
)
from return_rules import ReturnRequest
from sale_cancellation_rules import (
    Sale,
    SaleCancellationRequest,
    SaleCancellationStatus,
    SaleStatus,
    calculate_restoration_quantities,
    validate_request_approval,
)
from stock_adjustment_rules import validate_stock_adjustment
from stock_rules import (
    calculate_remaining_quantity,
    get_available_quantity,
    validate_available_quantity,
    validate_stock_availability,
)
from transfer_rules import (
    TransferStatus,
    calculate_dispatch_quantities,
    calculate_receipt_quantities,
    validate_branch_ids,
    validate_status_transition,
    validate_transfer_items,
)


class MovementType(str, Enum):
    """Available-stock movement effects supported by this planner."""

    SALE = "SALE"
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
    SALE_CANCELLATION = "SALE_CANCELLATION"
    RETURN_RESTOCK = "RETURN_RESTOCK"
    TRANSFER_DISPATCH = "TRANSFER_DISPATCH"
    TRANSFER_RECEIPT = "TRANSFER_RECEIPT"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


class MovementSource(str, Enum):
    """Source record category used to form stable effect references."""

    SALE = "SALE"
    PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
    SALE_CANCELLATION = "SALE_CANCELLATION"
    RETURN_INSPECTION = "RETURN_INSPECTION"
    TRANSFER = "TRANSFER"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


class MovementOrigin(str, Enum):
    """Whether an effect was initiated by an actor or trusted system."""

    ACTOR = "ACTOR"
    SYSTEM = "SYSTEM"


class InventoryDisposition(str, Enum):
    """Receipt classification; only AVAILABLE affects sellable stock."""

    AVAILABLE = "AVAILABLE"
    IN_TRANSIT = "IN_TRANSIT"
    UNDER_INSPECTION = "UNDER_INSPECTION"
    DAMAGED = "DAMAGED"


@dataclass(frozen=True)
class ProposedInventoryMovement:
    """One proposed signed change to available stock; never persisted here."""

    business_id: int
    branch_id: int
    product_variant_id: int
    movement_type: MovementType
    quantity_delta: int
    source_type: MovementSource
    source_reference: str
    origin: MovementOrigin
    actor_id: int | None = None
    system_origin: str | None = None
    reason: str | None = None

    @property
    def effect_reference(self):
        """Stable idempotency key for one source effect on one variant."""
        return json.dumps(
            [
                self.business_id,
                self.source_type.value,
                self.source_reference,
                self.movement_type.value,
                self.branch_id,
                self.product_variant_id,
            ],
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class ExpectedInventoryBalance:
    """Available quantity before and after all proposed effects in the plan."""

    business_id: int
    branch_id: int
    product_variant_id: int
    available_qty_before: int
    expected_available_qty: int


@dataclass(frozen=True)
class InventoryMovementPlan:
    """Immutable proposed effects and their cumulative available balances."""

    movements: tuple[ProposedInventoryMovement, ...]
    expected_balances: tuple[ExpectedInventoryBalance, ...]


@dataclass(frozen=True)
class TrustedSystemOrigin:
    """Marker for a trusted server-side system actor.

    Construct this only in trusted backend code, never from request fields.
    Operation builders still require trusted server-loaded resource scopes.
    """

    name: str


_EXPECTED_SOURCE = {
    MovementType.SALE: MovementSource.SALE,
    MovementType.PURCHASE_RECEIPT: MovementSource.PURCHASE_RECEIPT,
    MovementType.SALE_CANCELLATION: MovementSource.SALE_CANCELLATION,
    MovementType.RETURN_RESTOCK: MovementSource.RETURN_INSPECTION,
    MovementType.TRANSFER_DISPATCH: MovementSource.TRANSFER,
    MovementType.TRANSFER_RECEIPT: MovementSource.TRANSFER,
    MovementType.MANUAL_ADJUSTMENT: MovementSource.MANUAL_ADJUSTMENT,
}


def _positive_id(value, field_name):
    if not is_positive_integer(value):
        raise ValueError(f"{field_name} must be a positive integer.")


def _movement_identity(actor_id, system_origin=None):
    """Validate and normalize the trusted actor or system origin."""
    if (actor_id is None) == (system_origin is None):
        raise ValueError("Provide exactly one trusted actor_id or system_origin.")
    if actor_id is not None:
        _positive_id(actor_id, "actor_id")
        return MovementOrigin.ACTOR, actor_id, None
    if type(system_origin) is not TrustedSystemOrigin:
        raise ValueError("system_origin must be a trusted system origin.")
    if not isinstance(system_origin.name, str) or not system_origin.name.strip():
        raise ValueError("system origin name must be non-empty text.")
    return MovementOrigin.SYSTEM, None, system_origin.name.strip()


def _validate_branch_scope(branch_scope, business_id, branch_id):
    if not is_valid_resource_scope(branch_scope):
        raise ValueError("Invalid trusted branch resource context.")
    _positive_id(branch_scope.branch_id, "branch_id")
    if (branch_scope.business_id, branch_scope.branch_id) != (business_id, branch_id):
        raise ValueError("The branch scope does not match the trusted operation.")


def _variant_resource_scopes(variant_scopes, variant_ids):
    """Load and validate trusted product/variant scopes for each effect."""
    if not isinstance(variant_scopes, Mapping):
        raise ValueError("variant_scopes must map variant IDs to trusted scopes.")
    resolved = {}
    for variant_id in variant_ids:
        _positive_id(variant_id, "product_variant_id")
        if variant_id not in variant_scopes:
            raise ValueError("Trusted product and variant scopes are required.")
        scopes = variant_scopes[variant_id]
        if not isinstance(scopes, (tuple, list)) or len(scopes) != 2:
            raise ValueError("Each variant scope entry must contain product and variant scopes.")
        product_scope, variant_scope = scopes
        if not is_valid_resource_scope(product_scope) or not is_valid_resource_scope(
            variant_scope
        ):
            raise ValueError("Invalid trusted product or variant resource context.")
        resolved[variant_id] = (product_scope, variant_scope)
    return resolved


def _require_branch_product_access(
    principal,
    actor_id,
    branch_scope,
    variant_scopes,
    variant_ids,
    permission,
    *,
    system_origin=None,
):
    _movement_identity(actor_id, system_origin)
    if not is_valid_resource_scope(branch_scope) or not is_positive_integer(
        branch_scope.branch_id
    ):
        raise ValueError("Invalid trusted branch resource context.")
    resolved = _variant_resource_scopes(variant_scopes, variant_ids)

    if principal is None and system_origin is not None:
        business_id = branch_scope.business_id
        for product_scope, variant_scope in resolved.values():
            if not resources_belong_to_business(
                business_id, product_scope, variant_scope
            ):
                raise PermissionError(ACCESS_DENIED_MESSAGE)
        return branch_scope.business_id, branch_scope.branch_id, resolved

    if not is_trusted_principal(principal):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if principal.business_id != branch_scope.business_id:
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    for product_scope, variant_scope in resolved.values():
        require_resource_access(
            principal,
            permission,
            branch_scope,
            related_scopes=(product_scope, variant_scope),
        )
    return branch_scope.business_id, branch_scope.branch_id, resolved


def _require_purchase_access(
    principal, actor_id, branch_scope, variant_scopes, variant_ids
):
    if not is_trusted_principal(principal):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    permission_by_role = {
        Role.OWNER: PURCHASES_MANAGE,
        Role.BRANCH_MANAGER: PURCHASES_MANAGE_AUTHORIZED,
    }
    permission = permission_by_role.get(principal.role)
    if permission is None:
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    return _require_branch_product_access(
        principal,
        actor_id=actor_id,
        branch_scope=branch_scope,
        variant_scopes=variant_scopes,
        variant_ids=variant_ids,
        permission=permission,
    )


def _validated_items(items, *, allow_empty=False):
    """Reuse the public transfer item validator's shared ID/quantity contract."""
    return validate_transfer_items(items, allow_empty=allow_empty)


def _aggregate(items):
    quantities = {}
    for item in items:
        variant_id = item["product_variant_id"]
        quantities[variant_id] = quantities.get(variant_id, 0) + item["quantity"]
    return quantities


def _validated_disposition_items(items):
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError("Receipt items must be a non-empty list or tuple.")
    validated = _validated_items(items)
    result = []
    for original, item in zip(items, validated):
        if "disposition" not in original:
            raise ValueError("Each receipt item requires a disposition.")
        try:
            disposition = (
                original["disposition"]
                if isinstance(original["disposition"], InventoryDisposition)
                else InventoryDisposition(original["disposition"])
            )
        except (TypeError, ValueError):
            raise ValueError("Unknown inventory disposition.") from None
        result.append(
            {
                "product_variant_id": item["product_variant_id"],
                "quantity": item["quantity"],
                "disposition": disposition,
            }
        )
    return result


def _available_quantities(disposition_items):
    return _aggregate(
        [
            item
            for item in disposition_items
            if item["disposition"] is InventoryDisposition.AVAILABLE
        ]
    )


def _returned_variant_ids(returned_items):
    if not isinstance(returned_items, (list, tuple)) or not returned_items:
        raise ValueError("Returned items must be a non-empty list or tuple.")
    variant_ids = set()
    for item in returned_items:
        if isinstance(item, ReturnedItem):
            variant_id = item.product_variant_id
        elif isinstance(item, Mapping) and "product_variant_id" in item:
            variant_id = item["product_variant_id"]
        else:
            raise ValueError("Each returned item must identify a product variant.")
        _positive_id(variant_id, "product_variant_id")
        variant_ids.add(variant_id)
    return variant_ids


def _make_movements(
    *,
    business_id,
    branch_id,
    quantities,
    movement_type,
    source_type,
    source_reference,
    actor_id,
    system_origin=None,
    sign=1,
    reason=None,
):
    origin, normalized_actor_id, normalized_system_origin = _movement_identity(
        actor_id, system_origin
    )
    if not isinstance(source_reference, str) or not source_reference.strip():
        raise ValueError("source_reference must be non-empty text.")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty text when supplied.")
        reason = reason.strip()
    movements = []
    for variant_id, quantity in quantities.items():
        _positive_id(variant_id, "product_variant_id")
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("Movement quantity must be a positive integer.")
        movements.append(
            ProposedInventoryMovement(
                business_id=business_id,
                branch_id=branch_id,
                product_variant_id=variant_id,
                movement_type=movement_type,
                quantity_delta=sign * quantity,
                source_type=source_type,
                source_reference=source_reference.strip(),
                origin=origin,
                actor_id=normalized_actor_id,
                system_origin=normalized_system_origin,
                reason=reason,
            )
        )
    return tuple(movements)


def build_sale_movements(
    principal,
    *,
    actor_id,
    sale,
    branch_scope,
    variant_scopes,
    system_origin=None,
):
    """Propose available-stock deductions for a trusted completed sale."""
    if not isinstance(sale, Sale):
        raise ValueError("A trusted sale is required.")
    _positive_id(sale.sale_id, "sale_id")
    _positive_id(sale.business_id, "business_id")
    _positive_id(sale.branch_id, "branch_id")
    try:
        sale_status = (
            sale.status
            if isinstance(sale.status, SaleStatus)
            else SaleStatus(sale.status)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown sale status.") from None
    if type(sale.has_processed_returns) is not bool:
        raise ValueError("has_processed_returns must be a boolean.")
    if sale_status is not SaleStatus.COMPLETED:
        raise ValueError("Only completed sales can deduct inventory.")
    items = _validated_items(sale.items)
    quantities = _aggregate(items)
    _validate_branch_scope(branch_scope, sale.business_id, sale.branch_id)
    business_id, branch_id, _ = _require_branch_product_access(
        principal,
        actor_id,
        branch_scope,
        variant_scopes,
        quantities,
        SALES_RECORD,
        system_origin=system_origin,
    )
    return _make_movements(
        business_id=business_id,
        branch_id=branch_id,
        quantities=quantities,
        movement_type=MovementType.SALE,
        source_type=MovementSource.SALE,
        source_reference=str(sale.sale_id),
        actor_id=actor_id,
        system_origin=system_origin,
        sign=-1,
    )


def build_purchase_receipt_movements(
    principal,
    *,
    actor_id,
    purchase_id,
    receipt_id,
    branch_scope,
    variant_scopes,
    receipt_items,
):
    """Propose receipt increases only for items classified AVAILABLE.

    Items marked IN_TRANSIT, UNDER_INSPECTION, or DAMAGED produce no available
    movement. There are no standalone purchase rules in this project yet, so
    this function applies the existing trusted permission matrix and validates
    the confirmed receipt lines and their dispositions.
    """
    _positive_id(purchase_id, "purchase_id")
    _positive_id(receipt_id, "receipt_id")
    _positive_id(actor_id, "actor_id")
    lines = _validated_disposition_items(receipt_items)
    quantities = _available_quantities(lines)
    business_id, branch_id, _ = _require_purchase_access(
        principal,
        actor_id,
        branch_scope,
        variant_scopes,
        {item["product_variant_id"] for item in lines},
    )
    return _make_movements(
        business_id=business_id,
        branch_id=branch_id,
        quantities=quantities,
        movement_type=MovementType.PURCHASE_RECEIPT,
        source_type=MovementSource.PURCHASE_RECEIPT,
        source_reference=f"{purchase_id}:receipt:{receipt_id}",
        actor_id=actor_id,
        sign=1,
    )


def build_sale_cancellation_movements(
    principal,
    *,
    actor_id,
    cancellation_request,
    sale,
    branch_scope,
    variant_scopes,
):
    """Propose restoration for an authorized cancellation approval.

    A Pending request is checked through the existing approval rule and planned
    in its proposed Approved state. An already Approved request can also be
    planned, subject to the existing restoration rule and approval permission.
    The sale ID is the source reference because requests currently have no ID.
    """
    _positive_id(actor_id, "actor_id")
    if not isinstance(sale, Sale) or not isinstance(
        cancellation_request, SaleCancellationRequest
    ):
        raise ValueError("A trusted cancellation request and sale are required.")
    _validate_branch_scope(branch_scope, sale.business_id, sale.branch_id)
    try:
        request_status = (
            cancellation_request.status
            if isinstance(cancellation_request.status, SaleCancellationStatus)
            else SaleCancellationStatus(cancellation_request.status)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown sale cancellation request status.") from None
    if request_status is SaleCancellationStatus.PENDING:
        validate_request_approval(principal, cancellation_request, sale)
        approved_request = replace(
            cancellation_request, status=SaleCancellationStatus.APPROVED
        )
    elif request_status is SaleCancellationStatus.APPROVED:
        approved_request = cancellation_request
    else:
        raise ValueError("Only approved cancellation requests can restore inventory.")
    quantities = calculate_restoration_quantities(sale, approved_request)
    resolved = _variant_resource_scopes(variant_scopes, quantities)
    for product_scope, variant_scope in resolved.values():
        require_resource_access(
            principal,
            SALE_CANCELLATION_APPROVAL,
            branch_scope,
            related_scopes=(product_scope, variant_scope),
        )
    return _make_movements(
        business_id=sale.business_id,
        branch_id=sale.branch_id,
        quantities=quantities,
        movement_type=MovementType.SALE_CANCELLATION,
        source_type=MovementSource.SALE_CANCELLATION,
        source_reference=str(sale.sale_id),
        actor_id=actor_id,
        sign=1,
        reason=cancellation_request.reason,
    )


def build_resalable_return_movements(
    principal,
    *,
    actor_id,
    return_request,
    returned_items,
    inspection_id,
    inspection_items,
    branch_scope,
    variant_scopes,
    inspection_history=None,
):
    """Propose additions only for RESALABLE quantities from approved inspection."""
    _positive_id(actor_id, "actor_id")
    if not isinstance(return_request, ReturnRequest):
        raise ValueError("A trusted return request is required.")
    if not is_valid_resource_scope(branch_scope):
        raise ValueError("Invalid trusted branch resource context.")
    if (branch_scope.business_id, branch_scope.branch_id) != (
        return_request.business_id,
        return_request.branch_id,
    ):
        raise ValueError("The branch scope does not match the trusted return.")
    inspection_changes = calculate_inspection_inventory_changes(
        principal,
        return_request,
        returned_items,
        inspection_id,
        inspection_items,
        inspection_history=inspection_history,
    )
    quantities = inspection_changes.available_stock_additions
    resolved = _variant_resource_scopes(
        variant_scopes, _returned_variant_ids(returned_items)
    )
    for product_scope, variant_scope in resolved.values():
        require_resource_access(
            principal,
            RETURNS_INSPECT,
            branch_scope,
            related_scopes=(product_scope, variant_scope),
        )
    return _make_movements(
        business_id=return_request.business_id,
        branch_id=return_request.branch_id,
        quantities=quantities,
        movement_type=MovementType.RETURN_RESTOCK,
        source_type=MovementSource.RETURN_INSPECTION,
        source_reference=str(inspection_id),
        actor_id=actor_id,
        sign=1,
        reason=return_request.reason,
    )


def _transfer_variant_scopes(
    principal,
    actor_id,
    source_branch_scope,
    destination_branch_scope,
    variant_scopes,
    variant_ids,
    permission,
    permission_branch,
):
    _positive_id(actor_id, "actor_id")
    if not is_valid_resource_scope(source_branch_scope) or not is_valid_resource_scope(
        destination_branch_scope
    ):
        raise ValueError("Invalid trusted transfer branch context.")
    validate_branch_ids(
        source_branch_scope.branch_id, destination_branch_scope.branch_id
    )
    resolved = _variant_resource_scopes(variant_scopes, variant_ids)
    related_scopes = tuple(
        scope for pair in resolved.values() for scope in pair
    )
    if not can_access_transfer(
        principal,
        permission,
        source_branch_scope,
        destination_branch_scope,
        permission_branch=permission_branch,
        related_scopes=related_scopes,
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    return resolved


def build_transfer_dispatch_movements(
    principal,
    *,
    actor_id,
    transfer_id,
    current_status,
    source_branch_scope,
    destination_branch_scope,
    variant_scopes,
    items,
):
    """Propose source-branch deductions for a valid transfer dispatch."""
    _positive_id(transfer_id, "transfer_id")
    validated_items = _validated_items(items)
    quantities = calculate_dispatch_quantities(validated_items)
    validate_status_transition(current_status, TransferStatus.IN_TRANSIT)
    _transfer_variant_scopes(
        principal,
        actor_id,
        source_branch_scope,
        destination_branch_scope,
        variant_scopes,
        quantities,
        TRANSFERS_DISPATCH,
        "source",
    )
    return _make_movements(
        business_id=source_branch_scope.business_id,
        branch_id=source_branch_scope.branch_id,
        quantities=quantities,
        movement_type=MovementType.TRANSFER_DISPATCH,
        source_type=MovementSource.TRANSFER,
        source_reference=f"{transfer_id}:dispatch",
        actor_id=actor_id,
        sign=-1,
    )


def build_transfer_receipt_movements(
    principal,
    *,
    actor_id,
    transfer_id,
    receipt_id,
    current_status,
    source_branch_scope,
    destination_branch_scope,
    variant_scopes,
    receipt_items,
    dispatched_items,
    previously_received_items=None,
    complete_transfer=False,
):
    """Propose destination additions only for confirmed AVAILABLE receipt lines.

    Every disposition counts toward the transfer's received quantity, but only
    AVAILABLE lines increase sellable stock. A receipt can remain in transit;
    when this receipt completes the transfer, the existing completion rule is
    applied against cumulative confirmed quantities.
    """
    _positive_id(transfer_id, "transfer_id")
    _positive_id(receipt_id, "receipt_id")
    if type(complete_transfer) is not bool:
        raise ValueError("complete_transfer must be a boolean.")
    try:
        normalized_status = (
            current_status
            if isinstance(current_status, TransferStatus)
            else TransferStatus(current_status)
        )
    except (TypeError, ValueError):
        raise ValueError("Unknown transfer status.") from None
    if normalized_status is not TransferStatus.IN_TRANSIT:
        raise ValueError("Only in-transit transfers can receive stock.")

    disposition_items = _validated_disposition_items(receipt_items)
    normalized_items = [
        {
            "product_variant_id": item["product_variant_id"],
            "quantity": item["quantity"],
        }
        for item in disposition_items
    ]
    previous = [] if previously_received_items is None else _validated_items(
        previously_received_items, allow_empty=True
    )
    dispatched = _validated_items(dispatched_items)
    calculate_receipt_quantities(
        normalized_items,
        dispatched,
        previously_received_items=previous,
    )
    if complete_transfer:
        validate_status_transition(
            normalized_status,
            TransferStatus.COMPLETED,
            dispatched_items=dispatched,
            received_items=previous + normalized_items,
        )
    quantities = _available_quantities(disposition_items)
    _transfer_variant_scopes(
        principal,
        actor_id,
        source_branch_scope,
        destination_branch_scope,
        variant_scopes,
        {item["product_variant_id"] for item in disposition_items},
        TRANSFERS_CONFIRM_RECEIPT,
        "destination",
    )
    return _make_movements(
        business_id=destination_branch_scope.business_id,
        branch_id=destination_branch_scope.branch_id,
        quantities=quantities,
        movement_type=MovementType.TRANSFER_RECEIPT,
        source_type=MovementSource.TRANSFER,
        source_reference=f"{transfer_id}:receipt:{receipt_id}",
        actor_id=actor_id,
        sign=1,
    )


def build_manual_adjustment_movement(
    principal,
    *,
    actor_id,
    branch_scope,
    product_scope,
    variant_scope,
    product_variant_id,
    inventory_record,
    direction,
    quantity,
    reason,
    adjustment_reference,
    existing_adjustment_references=None,
):
    """Propose an authorized adjustment using the existing adjustment rules."""
    result = validate_stock_adjustment(
        principal,
        actor_id=actor_id,
        branch_scope=branch_scope,
        product_scope=product_scope,
        variant_scope=variant_scope,
        product_variant_id=product_variant_id,
        inventory_record=inventory_record,
        direction=direction,
        quantity=quantity,
        reason=reason,
        adjustment_reference=adjustment_reference,
        existing_adjustment_references=existing_adjustment_references,
    )
    reference = result.movement.adjustment_reference
    if reference is None:
        raise ValueError("adjustment_reference is required for idempotent movements.")
    movement_type = MovementType.MANUAL_ADJUSTMENT
    movement = ProposedInventoryMovement(
        business_id=result.movement.business_id,
        branch_id=result.movement.branch_id,
        product_variant_id=result.movement.product_variant_id,
        movement_type=movement_type,
        quantity_delta=result.quantity_change,
        source_type=MovementSource.MANUAL_ADJUSTMENT,
        source_reference=reference,
        origin=MovementOrigin.ACTOR,
        actor_id=actor_id,
        reason=result.movement.reason,
    )
    return (movement,)


def _validate_inventory_records(inventory_records):
    if inventory_records is None:
        return {}
    if not isinstance(inventory_records, Mapping):
        raise ValueError("inventory_records must map (branch_id, variant_id) to records.")
    normalized = {}
    for key, record in inventory_records.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("Inventory record keys must be (branch_id, product_variant_id).")
        branch_id, variant_id = key
        _positive_id(branch_id, "branch_id")
        _positive_id(variant_id, "product_variant_id")
        normalized[key] = get_available_quantity(record, branch_id, variant_id)
    return normalized


def _validate_movement(movement):
    if type(movement) is not ProposedInventoryMovement:
        raise ValueError("Each proposed movement must be a trusted movement effect.")
    _positive_id(movement.business_id, "business_id")
    _positive_id(movement.branch_id, "branch_id")
    _positive_id(movement.product_variant_id, "product_variant_id")
    if type(movement.movement_type) is not MovementType:
        raise ValueError("Unknown movement type.")
    if type(movement.source_type) is not MovementSource or (
        movement.source_type is not _EXPECTED_SOURCE[movement.movement_type]
    ):
        raise ValueError("Movement source does not match its movement type.")
    if type(movement.quantity_delta) is not int or movement.quantity_delta == 0:
        raise ValueError("quantity_delta must be a non-zero integer.")
    if movement.movement_type in {
        MovementType.SALE,
        MovementType.TRANSFER_DISPATCH,
    } and movement.quantity_delta > 0:
        raise ValueError("This movement type must decrease available stock.")
    if movement.movement_type in {
        MovementType.PURCHASE_RECEIPT,
        MovementType.SALE_CANCELLATION,
        MovementType.RETURN_RESTOCK,
        MovementType.TRANSFER_RECEIPT,
    } and movement.quantity_delta < 0:
        raise ValueError("This movement type must increase available stock.")
    if not isinstance(movement.source_reference, str) or not movement.source_reference.strip():
        raise ValueError("source_reference must be non-empty text.")
    if type(movement.origin) is not MovementOrigin:
        raise ValueError("Unknown movement origin.")
    if movement.origin is MovementOrigin.ACTOR:
        _positive_id(movement.actor_id, "actor_id")
        if movement.system_origin is not None:
            raise ValueError("Actor movements cannot include system_origin.")
    else:
        if movement.actor_id is not None:
            raise ValueError("System movements cannot include actor_id.")
        if not isinstance(movement.system_origin, str) or not movement.system_origin.strip():
            raise ValueError("System movements require a trusted system origin.")
    if movement.reason is not None and (
        not isinstance(movement.reason, str) or not movement.reason.strip()
    ):
        raise ValueError("reason must be non-empty text when supplied.")
    return movement


def _reference_set(references, field_name):
    if references is None:
        return set()
    if not isinstance(references, (tuple, list, set, frozenset)):
        raise ValueError(f"{field_name} must be a collection of non-empty strings.")
    values = []
    for reference in references:
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"{field_name} must contain non-empty strings.")
        values.append(reference.strip())
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} contains duplicate references.")
    return set(values)


def plan_inventory_movements(
    movements,
    inventory_records,
    *,
    existing_effect_references=None,
):
    """Return validated effects and cumulative balances without mutating inputs.

    Missing ``(branch_id, product_variant_id)`` records start at zero. Existing
    records are validated through ``stock_rules`` and only ``available_qty`` is
    read. Effects apply in the supplied order, and every intermediate balance
    must remain non-negative. A future database layer must recheck each effect
    reference under transaction and a locked balance row before appending it.
    """
    if not isinstance(movements, (list, tuple)):
        raise ValueError("movements must be a list or tuple.")
    records = _validate_inventory_records(inventory_records)
    processed_references = _reference_set(
        existing_effect_references, "existing_effect_references"
    )
    validated_movements = tuple(_validate_movement(item) for item in movements)
    if not validated_movements:
        return InventoryMovementPlan((), ())

    businesses = {item.business_id for item in validated_movements}
    if len(businesses) != 1:
        raise ValueError("One movement plan must belong to one business.")

    seen = set(processed_references)
    starting = {}
    current = {}
    for movement in validated_movements:
        reference = movement.effect_reference
        if reference in seen:
            raise ValueError("This inventory movement effect has already been processed.")
        seen.add(reference)
        key = (movement.branch_id, movement.product_variant_id)
        if key not in current:
            initial = records.get(key, 0)
            starting[key] = initial
            current[key] = initial
        available_now = current[key]
        if movement.quantity_delta < 0:
            deduction = -movement.quantity_delta
            validate_stock_availability(available_now, deduction)
            current[key] = calculate_remaining_quantity(available_now, deduction)
        else:
            current[key] = validate_available_quantity(
                available_now + movement.quantity_delta
            )

    business_id = next(iter(businesses))
    expected = tuple(
        ExpectedInventoryBalance(
            business_id=business_id,
            branch_id=branch_id,
            product_variant_id=variant_id,
            available_qty_before=starting[(branch_id, variant_id)],
            expected_available_qty=available,
        )
        for (branch_id, variant_id), available in current.items()
    )
    return InventoryMovementPlan(validated_movements, expected)


__all__ = [
    "ExpectedInventoryBalance",
    "InventoryDisposition",
    "InventoryMovementPlan",
    "MovementOrigin",
    "MovementSource",
    "MovementType",
    "ProposedInventoryMovement",
    "TrustedSystemOrigin",
    "build_manual_adjustment_movement",
    "build_purchase_receipt_movements",
    "build_resalable_return_movements",
    "build_sale_cancellation_movements",
    "build_sale_movements",
    "build_transfer_dispatch_movements",
    "build_transfer_receipt_movements",
    "plan_inventory_movements",
]
