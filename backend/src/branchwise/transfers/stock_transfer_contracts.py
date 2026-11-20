"""Internal read-only stock-transfer records and repository port."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol

from permissions import (
    TRANSFERS_APPROVE_SOURCE,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_DISPATCH,
)
from session_auth import AuthenticatedSession


TransferStatusRecord = Literal[
    "PENDING", "IN_TRANSIT", "COMPLETED", "REJECTED", "CANCELLED"
]


class TransferReadPurpose(str, Enum):
    """An operation-specific permission context for internal reads only.

    There is no standalone transfer-view permission in the approved matrix.
    These purposes must only be supplied by an already-authorized internal
    lifecycle caller; they do not grant general transfer-history access.
    """

    APPROVE_SOURCE = TRANSFERS_APPROVE_SOURCE
    DISPATCH = TRANSFERS_DISPATCH
    CONFIRM_RECEIPT = TRANSFERS_CONFIRM_RECEIPT

    @property
    def permission_branch(self) -> Literal["source", "destination"]:
        if self is TransferReadPurpose.CONFIRM_RECEIPT:
            return "destination"
        return "source"


@dataclass(frozen=True)
class StockTransferRecord:
    """Detached header values using the persisted stock_transfers columns."""

    id: int
    business_id: int
    transfer_reference: str
    request_key: str
    source_branch_id: int
    destination_branch_id: int
    requested_by_user_id: int
    status: TransferStatusRecord
    approved_by_user_id: int | None
    dispatched_by_user_id: int | None
    received_by_user_id: int | None
    requested_at: datetime
    dispatched_at: datetime | None
    received_at: datetime | None
    closed_at: datetime | None
    notes: str | None
    event_history_json: tuple[object, ...] | None


@dataclass(frozen=True)
class StockTransferItemRecord:
    """Detached item values using the persisted stock_transfer_items columns."""

    id: int
    business_id: int
    transfer_id: int
    variant_id: int
    requested_qty: int
    dispatched_qty: int
    received_qty: int
    returned_to_source_qty: int
    written_off_qty: int
    in_transit_qty: int
    missing_qty: int
    resolved_qty: int
    discrepancy_reported_by: int | None
    discrepancy_reported_at: datetime | None
    discrepancy_resolved_at: datetime | None
    discrepancy_notes: str | None


class StockTransferRepository(Protocol):
    """Scoped read port for transfer lifecycle callers, not a view API.

    Every operation requires an operation-specific purpose so a request,
    dispatch, or receipt grant is never treated as a general history-view
    permission. No purpose is exposed by a production route.
    """

    def get_stock_transfer(
        self,
        *,
        actor: AuthenticatedSession,
        transfer_id: int,
        purpose: TransferReadPurpose,
    ) -> StockTransferRecord: ...

    def get_stock_transfer_item(
        self,
        *,
        actor: AuthenticatedSession,
        item_id: int,
        purpose: TransferReadPurpose,
    ) -> StockTransferItemRecord: ...

    def list_stock_transfer_items(
        self,
        *,
        actor: AuthenticatedSession,
        transfer_id: int,
        purpose: TransferReadPurpose,
    ) -> tuple[StockTransferItemRecord, ...]: ...
