"""Read-only SQLAlchemy foundation for BE-29; deliberately not installed."""
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
from branchwise.purchasing.purchase_order_contracts import (
    PurchaseOrderItemRecord, PurchaseOrderRecord,
)
from branchwise.purchasing.purchase_order_model import purchase_order_items, purchase_orders


def _authorize(actor):
    if (type(actor) is not AuthenticatedSession
            or not is_positive_integer(actor.user_id)
            or not is_trusted_principal(actor.principal)):
        raise RepositoryAuthorizationError("Permission denied.")
    permission = (PURCHASES_MANAGE if actor.principal.role is Role.OWNER
                  else PURCHASES_MANAGE_AUTHORIZED)
    try:
        require_resource_access(actor.principal, permission, ResourceScope(
            actor.principal.business_id, actor.principal.branch_id,
        ))
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return actor.principal


def _validate_id(value, name):
    if not is_positive_integer(value):
        raise ValueError(f"{name} must be a positive integer.")


def _order_predicates(principal, purchase_order_id):
    predicates = [
        purchase_orders.c.business_id == principal.business_id,
        purchase_orders.c.id == purchase_order_id,
    ]
    if principal.role is not Role.OWNER:
        predicates.append(purchase_orders.c.branch_id == principal.branch_id)
    return predicates


def _order_record(row):
    values = dict(row)
    values["purchase_order_id"] = values.pop("id")
    return PurchaseOrderRecord(**values)


def _item_record(row):
    values = dict(row)
    values["item_id"] = values.pop("id")
    return PurchaseOrderItemRecord(**values)


class SQLAlchemyPurchaseOrderRepository:
    """Own each read transaction using a factory returning fresh idle Sessions.

    Follows the supplier adapter's session ownership convention. Do not inject
    db.session or a caller-owned unit of work. Only SELECT statements are used;
    no preparation result can trigger persistence through this adapter.
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

    def get_purchase_order(
        self, *, actor: AuthenticatedSession, purchase_order_id: int
    ) -> PurchaseOrderRecord:
        principal = _authorize(actor)
        _validate_id(purchase_order_id, "purchase_order_id")
        try:
            with self._transaction() as session:
                row = session.execute(select(purchase_orders).where(
                    *_order_predicates(principal, purchase_order_id),
                )).mappings().one_or_none()
                if row is None:
                    raise RepositoryNotFoundError("Purchase order not found.")
                saved = _order_record(row)
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError("Purchase order could not be loaded.") from None

    @staticmethod
    def _items_statement(principal, purchase_order_id):
        return select(purchase_order_items).select_from(purchase_order_items.join(
            purchase_orders,
            (purchase_order_items.c.business_id == purchase_orders.c.business_id)
            & (purchase_order_items.c.purchase_order_id == purchase_orders.c.id),
        )).where(
            *_order_predicates(principal, purchase_order_id),
            purchase_order_items.c.business_id == principal.business_id,
            purchase_order_items.c.purchase_order_id == purchase_order_id,
        )

    def get_purchase_order_item(
        self, *, actor: AuthenticatedSession, purchase_order_id: int, item_id: int
    ) -> PurchaseOrderItemRecord:
        principal = _authorize(actor)
        _validate_id(purchase_order_id, "purchase_order_id")
        _validate_id(item_id, "item_id")
        try:
            with self._transaction() as session:
                row = session.execute(self._items_statement(principal, purchase_order_id).where(
                    purchase_order_items.c.id == item_id,
                )).mappings().one_or_none()
                if row is None:
                    raise RepositoryNotFoundError("Purchase order item not found.")
                saved = _item_record(row)
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError("Purchase order items could not be loaded.") from None

    def list_purchase_order_items(
        self, *, actor: AuthenticatedSession, purchase_order_id: int
    ) -> tuple[PurchaseOrderItemRecord, ...]:
        principal = _authorize(actor)
        _validate_id(purchase_order_id, "purchase_order_id")
        try:
            with self._transaction() as session:
                order = session.execute(select(purchase_orders.c.id).where(
                    *_order_predicates(principal, purchase_order_id),
                )).one_or_none()
                if order is None:
                    raise RepositoryNotFoundError("Purchase order not found.")
                rows = session.execute(self._items_statement(principal, purchase_order_id).order_by(
                    purchase_order_items.c.id,
                )).mappings()
                saved = tuple(_item_record(row) for row in rows)
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError("Purchase order items could not be loaded.") from None
