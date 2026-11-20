"""Read-only BE-30 persistence port; no receiving command or API installation."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from session_auth import AuthenticatedSession


@dataclass(frozen=True)
class PurchaseReceiptRecord:
    """Detached header plus destination branch loaded from its purchase order."""

    receipt_id: int
    business_id: int
    purchase_order_id: int
    branch_id: int
    receipt_reference: str
    request_key: str
    received_by_user_id: int
    received_at: datetime
    notes: str | None


@dataclass(frozen=True)
class PurchaseReceiptItemRecord:
    """Detached line verified against the header's business and purchase order."""

    item_id: int
    business_id: int
    purchase_receipt_id: int
    purchase_order_item_id: int
    received_qty: int


class PurchaseReceivingRepository(Protocol):
    """Trusted-session reads using existing purchasing role/grant conventions.

    Owners require purchases:manage; managers require purchases:manage_authorized
    and their assigned destination branch; employees are denied. The actor must
    be server-resolved, never constructed from client identity or scope fields.
    Missing and inaccessible resources share RepositoryNotFoundError. The header
    provides the associated purchase_order_id, business_id and stored branch_id;
    no client-supplied branch or order ID can authorize a lookup.

    Every read verifies ALL lines of the authorized receipt, even header reads.
    Separate schema FKs do not enforce PO membership: mismatched or unresolved
    PO-item links raise generic RepositoryPersistenceError, with no partial list
    or header returned. No lifecycle or receiving-quantity policy is inferred.
    Reads own fresh idle sessions/transactions and return immutable values only.
    """

    def get_purchase_receipt(
        self, *, actor: AuthenticatedSession, receipt_id: int
    ) -> PurchaseReceiptRecord: ...

    def get_purchase_receipt_item(
        self, *, actor: AuthenticatedSession, receipt_id: int, item_id: int
    ) -> PurchaseReceiptItemRecord: ...

    def list_purchase_receipt_items(
        self, *, actor: AuthenticatedSession, receipt_id: int
    ) -> tuple[PurchaseReceiptItemRecord, ...]:
        """ID-ordered lines; an accessible empty receipt returns ()."""
        ...
