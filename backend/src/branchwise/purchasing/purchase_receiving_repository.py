"""Scoped read-only BE-30 adapter; deliberately not installed in production."""
from contextlib import contextmanager
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from isolation_rules import require_resource_access
from permissions import (
    PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED, ResourceScope, Role,
    is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryNotFoundError, RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from branchwise.purchasing.purchase_order_model import purchase_order_items, purchase_orders
from branchwise.purchasing.purchase_receiving_contracts import (
    PurchaseReceiptItemRecord, PurchaseReceiptRecord,
)
from branchwise.purchasing.purchase_receiving_model import purchase_receipt_items, purchase_receipts


_LOAD_ERROR = "Purchase receiving records could not be loaded."


def _authorize(actor):
    if (type(actor) is not AuthenticatedSession
            or not is_positive_integer(actor.user_id)
            or not is_trusted_principal(actor.principal)):
        raise RepositoryAuthorizationError("Permission denied.")
    principal = actor.principal
    permission = (PURCHASES_MANAGE if principal.role is Role.OWNER
                  else PURCHASES_MANAGE_AUTHORIZED)
    try:
        require_resource_access(principal, permission, ResourceScope(
            principal.business_id, principal.branch_id,
        ))
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return principal


def _validate_id(value, name):
    if not is_positive_integer(value):
        raise ValueError(f"{name} must be a positive integer.")


def _receipt_join():
    return purchase_receipts.join(
        purchase_orders,
        (purchase_receipts.c.business_id == purchase_orders.c.business_id)
        & (purchase_receipts.c.purchase_order_id == purchase_orders.c.id),
    )


def _scope_predicates(principal, receipt_id):
    predicates = [
        purchase_receipts.c.business_id == principal.business_id,
        purchase_receipts.c.id == receipt_id,
        purchase_orders.c.business_id == principal.business_id,
    ]
    if principal.role is not Role.OWNER:
        predicates.append(purchase_orders.c.branch_id == principal.branch_id)
    return predicates


class SQLAlchemyPurchaseReceivingRepository:
    """Inject a factory returning fresh idle Sessions, never a caller's UoW.

    Follows BE-29 session ownership: only SELECT statements, no autoflush of
    caller work, results released after successful transaction exit. All receipt
    lines are checked before any result escapes, closing the approved schema's
    missing receipt-to-PO-line membership constraint without changing its DDL.
    These reads establish no locking or concurrent receiving guarantees.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        if not callable(session_factory):
            raise TypeError("session_factory must be callable.")
        self._session_factory = session_factory

    @contextmanager
    def _transaction(self):
        session = self._session_factory()
        if not isinstance(session, Session):
            raise TypeError("session_factory must return a SQLAlchemy Session.")
        if session.in_transaction() or session.new or session.dirty or session.deleted:
            raise RepositoryPersistenceError("A fresh database session is required.")
        with session:
            with session.begin():
                yield session

    def _read(self, principal, receipt_id):
        try:
            with self._transaction() as session:
                header = session.execute(select(
                    purchase_receipts, purchase_orders.c.branch_id,
                ).select_from(_receipt_join()).where(
                    *_scope_predicates(principal, receipt_id),
                )).mappings().one_or_none()
                if header is None:
                    raise RepositoryNotFoundError("Purchase receipt not found.")

                # Keep mismatched links visible: joining on order membership
                # would silently drop bad lines and return a misleading subset.
                source = _receipt_join().join(
                    purchase_receipt_items,
                    (purchase_receipt_items.c.business_id == purchase_receipts.c.business_id)
                    & (purchase_receipt_items.c.purchase_receipt_id == purchase_receipts.c.id),
                ).outerjoin(
                    purchase_order_items,
                    (purchase_receipt_items.c.business_id == purchase_order_items.c.business_id)
                    & (purchase_receipt_items.c.purchase_order_item_id == purchase_order_items.c.id),
                )
                rows = session.execute(select(
                    purchase_receipt_items,
                    purchase_order_items.c.purchase_order_id.label("linked_order_id"),
                ).select_from(source).where(
                    *_scope_predicates(principal, receipt_id),
                    purchase_receipt_items.c.business_id == principal.business_id,
                ).order_by(purchase_receipt_items.c.id)).mappings()
                items = []
                for row in rows:
                    values = dict(row)
                    if values.pop("linked_order_id") != header["purchase_order_id"]:
                        raise RepositoryPersistenceError(_LOAD_ERROR)
                    values["item_id"] = values.pop("id")
                    items.append(PurchaseReceiptItemRecord(**values))
                values = dict(header)
                values["receipt_id"] = values.pop("id")
                receipt = PurchaseReceiptRecord(**values)
            return receipt, tuple(items)
        except SQLAlchemyError:
            raise RepositoryPersistenceError(_LOAD_ERROR) from None

    def get_purchase_receipt(
        self, *, actor: AuthenticatedSession, receipt_id: int
    ) -> PurchaseReceiptRecord:
        principal = _authorize(actor)
        _validate_id(receipt_id, "receipt_id")
        return self._read(principal, receipt_id)[0]

    def get_purchase_receipt_item(
        self, *, actor: AuthenticatedSession, receipt_id: int, item_id: int
    ) -> PurchaseReceiptItemRecord:
        principal = _authorize(actor)
        _validate_id(receipt_id, "receipt_id")
        _validate_id(item_id, "item_id")
        _, items = self._read(principal, receipt_id)
        for item in items:
            if item.item_id == item_id:
                return item
        raise RepositoryNotFoundError("Purchase receipt item not found.")

    def list_purchase_receipt_items(
        self, *, actor: AuthenticatedSession, receipt_id: int
    ) -> tuple[PurchaseReceiptItemRecord, ...]:
        principal = _authorize(actor)
        _validate_id(receipt_id, "receipt_id")
        return self._read(principal, receipt_id)[1]
