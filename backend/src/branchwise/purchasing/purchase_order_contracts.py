"""Read-only BE-29 persistence port; no creation command or API installation."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from session_auth import AuthenticatedSession


PurchaseOrderStatus = Literal["PENDING", "PARTIALLY_RECEIVED", "RECEIVED"]


@dataclass(frozen=True)
class PurchaseOrderRecord:
    """Detached stored order header, with exact database monetary values."""

    purchase_order_id: int
    business_id: int
    branch_id: int
    supplier_id: int
    purchase_reference: str
    status: PurchaseOrderStatus
    ordered_by_user_id: int
    ordered_at: datetime
    expected_total_cost: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PurchaseOrderItemRecord:
    """Detached stored line; no ORM state, prices inferred, or totals recomputed."""

    item_id: int
    business_id: int
    purchase_order_id: int
    variant_id: int
    ordered_qty: int
    unit_cost: Decimal
    line_total: Decimal


class PurchaseOrderRepository(Protocol):
    """Authorize trusted actors and scope every lookup by business and branch.

    Owners need purchases:manage; managers need purchases:manage_authorized
    and their assigned branch. Employees cannot use this port. Missing and
    inaccessible resources share RepositoryNotFoundError. Reads own fresh idle
    sessions/transactions, never a caller's unit of work, and return detached
    immutable records. Line lookups must also scope the parent order.

    Creation and lifecycle writes are intentionally absent: preparation does
    not establish authoritative prices, variant resolution or references.
    """

    def get_purchase_order(
        self, *, actor: AuthenticatedSession, purchase_order_id: int
    ) -> PurchaseOrderRecord: ...

    def get_purchase_order_item(
        self, *, actor: AuthenticatedSession, purchase_order_id: int, item_id: int
    ) -> PurchaseOrderItemRecord: ...

    def list_purchase_order_items(
        self, *, actor: AuthenticatedSession, purchase_order_id: int
    ) -> tuple[PurchaseOrderItemRecord, ...]:
        """Return lines in ID order; an existing empty order returns ()."""
        ...
