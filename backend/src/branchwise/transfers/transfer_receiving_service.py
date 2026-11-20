"""Internal, non-persistent preparation for BE-36 transfer receiving."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal

from branchwise.transfers.stock_transfer_contracts import (
    StockTransferItemRecord,
    StockTransferRecord,
    StockTransferRepository,
    TransferReadPurpose,
    TransferStatusRecord,
)
from branchwise.transfers.transfer_receiving_validation import (
    validate_transfer_receiving_input,
)
from isolation_rules import can_access_transfer, require_resource_access
from permissions import (
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    TRANSFERS_CONFIRM_RECEIPT,
    is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from transfer_rules import (
    TransferStatus,
    calculate_quantity_discrepancies,
    calculate_receipt_quantities,
)


_PREPARATION_ERROR = "Transfer receiving could not be prepared."
_UINT32_MAX = (1 << 32) - 1


class TransferReceivingPreparationState(str, Enum):
    PREPARED_FOR_REVIEW = "prepared_for_review_not_persisted"
    BLOCKED = "blocked"


class TransferReceivingBlocker(str, Enum):
    NOT_DISPATCHED = "transfer_not_dispatched"
    STATUS_NOT_RECEIVABLE = "transfer_status_not_receivable"
    NO_IN_TRANSIT_QUANTITY = "no_in_transit_quantity"
    UNKNOWN_TRANSFER_VARIANT = "unknown_transfer_variant"
    DUPLICATE_VARIANT = "duplicate_variant"
    QUANTITY_EXCEEDS_IN_TRANSIT = "quantity_exceeds_in_transit"
    PARTIAL_RECEIPT_POLICY_UNRESOLVED = "partial_receipt_policy_unresolved"
    DISCREPANCY_POLICY_UNRESOLVED = "discrepancy_policy_unresolved"
    QUANTITY_RESOLUTION_POLICY_UNRESOLVED = "quantity_resolution_policy_unresolved"


@dataclass(frozen=True)
class TransferReceiptLinePreparation:
    transfer_item_id: int
    product_variant_id: int
    dispatched_qty: int
    previously_received_qty: int
    receipt_qty: int
    in_transit_qty_before: int
    in_transit_qty_after: int


@dataclass(frozen=True)
class TransferReceivingPreparation:
    """Authorized receipt quantities, not a confirmation or stock reservation."""

    transfer_id: int
    expected_current_status: TransferStatusRecord
    state: TransferReceivingPreparationState
    lines: tuple[TransferReceiptLinePreparation, ...]
    blocker: TransferReceivingBlocker | None
    persisted: Literal[False] = False
    stock_reserved: Literal[False] = False
    disposition_required_before_inventory_effect: Literal[True] = True


class TransferReceivingInputError(ValueError):
    """Raised when the existing BE-36 receiving envelope is invalid."""

    def __init__(self, field_errors):
        self.field_errors = MappingProxyType(dict(field_errors))
        super().__init__("Transfer receiving input is invalid.")


def _authorize_actor(actor: AuthenticatedSession) -> AuthenticatedPrincipal:
    if (
        type(actor) is not AuthenticatedSession
        or not is_positive_integer(actor.user_id)
        or not is_trusted_principal(actor.principal)
        or actor.principal.role not in (Role.OWNER, Role.BRANCH_MANAGER)
    ):
        raise RepositoryAuthorizationError("Permission denied.")

    principal = actor.principal
    try:
        require_resource_access(
            principal,
            TRANSFERS_CONFIRM_RECEIPT,
            ResourceScope(principal.business_id, principal.branch_id),
        )
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return principal


def _blocked(
    transfer_id: int,
    current_status: TransferStatusRecord,
    blocker: TransferReceivingBlocker,
) -> TransferReceivingPreparation:
    return TransferReceivingPreparation(
        transfer_id=transfer_id,
        expected_current_status=current_status,
        state=TransferReceivingPreparationState.BLOCKED,
        lines=(),
        blocker=blocker,
    )


def _persisted_quantity(value, name, *, positive=False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum or value > _UINT32_MAX:
        raise RepositoryPersistenceError(_PREPARATION_ERROR)
    return value


def _validate_transfer_items(
    items: tuple[StockTransferItemRecord, ...],
    transfer: StockTransferRecord,
) -> dict[int, StockTransferItemRecord]:
    if type(items) is not tuple or not items:
        raise RepositoryPersistenceError(_PREPARATION_ERROR)

    by_variant = {}
    seen_item_ids = set()
    for item in items:
        if type(item) is not StockTransferItemRecord:
            raise RepositoryPersistenceError(_PREPARATION_ERROR)
        if (
            not is_positive_integer(item.id)
            or not is_positive_integer(item.business_id)
            or not is_positive_integer(item.transfer_id)
            or not is_positive_integer(item.variant_id)
            or item.business_id != transfer.business_id
            or item.transfer_id != transfer.id
            or item.id in seen_item_ids
            or item.variant_id in by_variant
        ):
            raise RepositoryPersistenceError(_PREPARATION_ERROR)
        seen_item_ids.add(item.id)
        by_variant[item.variant_id] = item

        requested = _persisted_quantity(item.requested_qty, "requested_qty", positive=True)
        dispatched = _persisted_quantity(item.dispatched_qty, "dispatched_qty")
        received = _persisted_quantity(item.received_qty, "received_qty")
        returned = _persisted_quantity(item.returned_to_source_qty, "returned_to_source_qty")
        written_off = _persisted_quantity(item.written_off_qty, "written_off_qty")
        in_transit = _persisted_quantity(item.in_transit_qty, "in_transit_qty")
        missing = _persisted_quantity(item.missing_qty, "missing_qty")
        resolved = _persisted_quantity(item.resolved_qty, "resolved_qty")

        if (
            dispatched > requested
            or dispatched != received + returned + written_off + in_transit
            or resolved > missing
            or missing > dispatched
            or (missing and (item.discrepancy_reported_by is None
                             or item.discrepancy_reported_at is None))
        ):
            raise RepositoryPersistenceError(_PREPARATION_ERROR)
    return by_variant


def _transfer_status(transfer: StockTransferRecord) -> TransferStatus:
    try:
        return TransferStatus[transfer.status]
    except (KeyError, TypeError):
        raise RepositoryPersistenceError(_PREPARATION_ERROR) from None


def prepare_transfer_receiving(
    *,
    actor: AuthenticatedSession,
    receiving_input: object,
    repository: StockTransferRepository,
) -> TransferReceivingPreparation:
    """Prepare a destination-side receipt from trusted persisted transfer data.

    Only an IN_TRANSIT transfer with a complete, non-discrepant receipt of its
    current in-transit quantities can be prepared. Partial receipt and quantity
    resolution remain blocked pending approved persistence policy. This method
    never calculates or applies inventory movements.
    """
    principal = _authorize_actor(actor)

    errors = validate_transfer_receiving_input(receiving_input)
    if errors:
        raise TransferReceivingInputError(errors)
    if not isinstance(receiving_input, dict):
        raise TransferReceivingInputError(
            {"body": "Transfer receiving input must be a dictionary."}
        )
    if not callable(getattr(repository, "get_stock_transfer", None)) or not callable(
        getattr(repository, "list_stock_transfer_items", None)
    ):
        raise TypeError("repository must provide transfer and item read operations.")

    transfer_id = receiving_input["transfer_id"]
    receipt_items = [
        {
            "product_variant_id": item["product_variant_id"],
            "quantity": item["quantity"],
        }
        for item in receiving_input["items"]
    ]
    transfer = repository.get_stock_transfer(
        actor=actor,
        transfer_id=transfer_id,
        purpose=TransferReadPurpose.CONFIRM_RECEIPT,
    )

    if type(transfer.id) is not int or transfer.id != transfer_id:
        raise RepositoryNotFoundError("Stock transfer not found.")
    if transfer.business_id != principal.business_id:
        raise RepositoryNotFoundError("Stock transfer not found.")
    if not can_access_transfer(
        principal,
        TRANSFERS_CONFIRM_RECEIPT,
        ResourceScope(transfer.business_id, transfer.source_branch_id),
        ResourceScope(transfer.business_id, transfer.destination_branch_id),
        permission_branch="destination",
    ):
        raise RepositoryNotFoundError("Stock transfer not found.")

    current_status = _transfer_status(transfer)
    if current_status is TransferStatus.PENDING:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.NOT_DISPATCHED,
        )
    if current_status is not TransferStatus.IN_TRANSIT:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.STATUS_NOT_RECEIVABLE,
        )

    persisted_items = repository.list_stock_transfer_items(
        actor=actor,
        transfer_id=transfer_id,
        purpose=TransferReadPurpose.CONFIRM_RECEIPT,
    )
    items_by_variant = _validate_transfer_items(persisted_items, transfer)

    requested_by_variant = {}
    for item in receipt_items:
        variant_id = item["product_variant_id"]
        if variant_id in requested_by_variant:
            return _blocked(
                transfer_id,
                transfer.status,
                TransferReceivingBlocker.DUPLICATE_VARIANT,
            )
        requested_by_variant[variant_id] = item["quantity"]

    if any(variant_id not in items_by_variant for variant_id in requested_by_variant):
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.UNKNOWN_TRANSFER_VARIANT,
        )

    dispatched_items = [
        {"product_variant_id": item.variant_id, "quantity": item.dispatched_qty}
        for item in persisted_items
        if item.dispatched_qty > 0
    ]
    previously_received_items = [
        {"product_variant_id": item.variant_id, "quantity": item.received_qty}
        for item in persisted_items
        if item.received_qty > 0
    ]
    in_transit_by_variant = {
        item.variant_id: item.in_transit_qty
        for item in persisted_items
        if item.in_transit_qty > 0
    }
    if not dispatched_items or not in_transit_by_variant:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.NO_IN_TRANSIT_QUANTITY,
        )

    if any(
        quantity > in_transit_by_variant.get(variant_id, 0)
        for variant_id, quantity in requested_by_variant.items()
    ):
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.QUANTITY_EXCEEDS_IN_TRANSIT,
        )

    try:
        calculated_quantities = calculate_receipt_quantities(
            receipt_items,
            dispatched_items,
            previously_received_items=previously_received_items,
        )
    except ValueError:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.QUANTITY_EXCEEDS_IN_TRANSIT,
        )

    has_unresolved_discrepancy = any(
        item.missing_qty > item.resolved_qty for item in persisted_items
    )
    if has_unresolved_discrepancy:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.DISCREPANCY_POLICY_UNRESOLVED,
        )

    if requested_by_variant != in_transit_by_variant:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.PARTIAL_RECEIPT_POLICY_UNRESOLVED,
        )

    if any(
        item.returned_to_source_qty or item.written_off_qty
        for item in persisted_items
    ):
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.QUANTITY_RESOLUTION_POLICY_UNRESOLVED,
        )

    received_to_date = previously_received_items + receipt_items
    try:
        discrepancies = calculate_quantity_discrepancies(
            dispatched_items, received_to_date
        )
    except ValueError:
        raise RepositoryPersistenceError(_PREPARATION_ERROR) from None
    if discrepancies:
        return _blocked(
            transfer_id,
            transfer.status,
            TransferReceivingBlocker.DISCREPANCY_POLICY_UNRESOLVED,
        )

    lines = tuple(
        TransferReceiptLinePreparation(
            transfer_item_id=items_by_variant[variant_id].id,
            product_variant_id=variant_id,
            dispatched_qty=items_by_variant[variant_id].dispatched_qty,
            previously_received_qty=items_by_variant[variant_id].received_qty,
            receipt_qty=calculated_quantities[variant_id],
            in_transit_qty_before=items_by_variant[variant_id].in_transit_qty,
            in_transit_qty_after=(
                items_by_variant[variant_id].in_transit_qty
                - calculated_quantities[variant_id]
            ),
        )
        for variant_id in requested_by_variant
    )
    return TransferReceivingPreparation(
        transfer_id=transfer_id,
        expected_current_status=transfer.status,
        state=TransferReceivingPreparationState.PREPARED_FOR_REVIEW,
        lines=lines,
        blocker=None,
    )
