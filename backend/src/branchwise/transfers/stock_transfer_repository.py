"""Internal, SELECT-only repository for stock-transfer records."""

from contextlib import contextmanager
from datetime import datetime
import math
from types import MappingProxyType
from typing import Callable

from sqlalchemy import and_, column, select, table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from branchwise.transfers.stock_transfer_contracts import (
    StockTransferItemRecord,
    StockTransferRecord,
    TransferReadPurpose,
    TransferStatusRecord,
)
from branchwise.transfers.stock_transfer_model import (
    stock_transfer_items,
    stock_transfers,
)
from branchwise.suppliers.supplier_model import _identifier_type
from isolation_rules import can_access_transfer, require_resource_access
from permissions import (
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from transfer_rules import TransferStatus, validate_branch_ids, validate_transfer_items


_LOAD_ERROR = "Stock transfer records could not be loaded."
_TRANSFER_NOT_FOUND = "Stock transfer not found."
_ITEM_NOT_FOUND = "Stock transfer item not found."
_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_PERSISTED_STATUSES = frozenset(status.name for status in TransferStatus)

_branches = table(
    "branches",
    column("business_id", _identifier_type()),
    column("id", _identifier_type()),
)
_source_branch = _branches.alias("stock_transfer_source_branch")
_destination_branch = _branches.alias("stock_transfer_destination_branch")
_product_variants = table(
    "product_variants",
    column("business_id", _identifier_type()),
    column("id", _identifier_type()),
)


def _authorize(actor, purpose):
    if (
        type(actor) is not AuthenticatedSession
        or not is_positive_integer(actor.user_id)
        or not is_trusted_principal(actor.principal)
        or type(purpose) is not TransferReadPurpose
    ):
        raise RepositoryAuthorizationError("Permission denied.")

    principal = actor.principal
    try:
        require_resource_access(
            principal,
            purpose.value,
            ResourceScope(principal.business_id, principal.branch_id),
        )
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return principal


def _validate_id(value, field_name):
    if not is_positive_integer(value):
        raise ValueError(f"{field_name} must be a positive integer.")


def _read_scope_predicates(principal, purpose, transfer_table=stock_transfers):
    predicates = [transfer_table.c.business_id == principal.business_id]
    if principal.role is Role.BRANCH_MANAGER:
        branch_column = (
            transfer_table.c.destination_branch_id
            if purpose.permission_branch == "destination"
            else transfer_table.c.source_branch_id
        )
        predicates.append(branch_column == principal.branch_id)
    return predicates


def _transfer_statement(principal, purpose, transfer_id):
    return (
        select(stock_transfers)
        .select_from(
            stock_transfers.join(
                _source_branch,
                and_(
                    _source_branch.c.business_id == stock_transfers.c.business_id,
                    _source_branch.c.id == stock_transfers.c.source_branch_id,
                ),
            ).join(
                _destination_branch,
                and_(
                    _destination_branch.c.business_id == stock_transfers.c.business_id,
                    _destination_branch.c.id
                    == stock_transfers.c.destination_branch_id,
                ),
            )
        )
        .where(
            *_read_scope_predicates(principal, purpose),
            stock_transfers.c.id == transfer_id,
        )
    )


def _item_statement(principal, purpose, *, item_id=None, transfer_id=None):
    statement = (
        select(
            stock_transfer_items,
            _product_variants.c.id.label("_joined_variant_id"),
        )
        .select_from(
            stock_transfer_items.join(
                stock_transfers,
                and_(
                    stock_transfer_items.c.business_id
                    == stock_transfers.c.business_id,
                    stock_transfer_items.c.transfer_id == stock_transfers.c.id,
                ),
            )
            .join(
                _source_branch,
                and_(
                    _source_branch.c.business_id == stock_transfers.c.business_id,
                    _source_branch.c.id == stock_transfers.c.source_branch_id,
                ),
            )
            .join(
                _destination_branch,
                and_(
                    _destination_branch.c.business_id == stock_transfers.c.business_id,
                    _destination_branch.c.id
                    == stock_transfers.c.destination_branch_id,
                ),
            )
            .outerjoin(
                _product_variants,
                and_(
                    _product_variants.c.business_id
                    == stock_transfer_items.c.business_id,
                    _product_variants.c.id == stock_transfer_items.c.variant_id,
                ),
            )
        )
        .where(
            stock_transfer_items.c.business_id == principal.business_id,
            *_read_scope_predicates(principal, purpose),
        )
    )
    if item_id is not None:
        statement = statement.where(stock_transfer_items.c.id == item_id)
    if transfer_id is not None:
        statement = statement.where(stock_transfers.c.id == transfer_id)
    return statement.order_by(stock_transfer_items.c.id)


def _unsigned_integer(value, field_name, maximum):
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f"Stored {field_name} is invalid.")
    return value


def _identifier(value, field_name, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or value <= 0 or value > _UINT64_MAX:
        raise ValueError(f"Stored {field_name} is invalid.")
    return value


def _text(value, field_name, maximum=None, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not str or (maximum is not None and len(value) > maximum):
        raise ValueError(f"Stored {field_name} is invalid.")
    return value


def _timestamp(value, field_name, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, datetime):
        raise ValueError(f"Stored {field_name} is invalid.")
    return value


def _freeze_json(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Stored event history JSON is invalid.")
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("Stored event history JSON is invalid.")
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    raise ValueError("Stored event history JSON is invalid.")


def _transfer_record(row):
    transfer_id = _identifier(row["id"], "id")
    business_id = _identifier(row["business_id"], "business_id")
    source_branch_id = _identifier(row["source_branch_id"], "source_branch_id")
    destination_branch_id = _identifier(
        row["destination_branch_id"], "destination_branch_id"
    )
    validate_branch_ids(source_branch_id, destination_branch_id)

    status = row["status"]
    if type(status) is not str or status not in _PERSISTED_STATUSES:
        raise ValueError("Stored transfer status is invalid.")

    event_history = row["event_history_json"]
    if event_history is not None:
        if not isinstance(event_history, list):
            raise ValueError("Stored event history JSON must be an array.")
        event_history = tuple(_freeze_json(item) for item in event_history)

    return StockTransferRecord(
        id=transfer_id,
        business_id=business_id,
        transfer_reference=_text(row["transfer_reference"], "transfer_reference", 40),
        request_key=_text(row["request_key"], "request_key", 120),
        source_branch_id=source_branch_id,
        destination_branch_id=destination_branch_id,
        requested_by_user_id=_identifier(
            row["requested_by_user_id"], "requested_by_user_id"
        ),
        status=status,
        approved_by_user_id=_identifier(
            row["approved_by_user_id"], "approved_by_user_id", nullable=True
        ),
        dispatched_by_user_id=_identifier(
            row["dispatched_by_user_id"], "dispatched_by_user_id", nullable=True
        ),
        received_by_user_id=_identifier(
            row["received_by_user_id"], "received_by_user_id", nullable=True
        ),
        requested_at=_timestamp(row["requested_at"], "requested_at"),
        dispatched_at=_timestamp(
            row["dispatched_at"], "dispatched_at", nullable=True
        ),
        received_at=_timestamp(row["received_at"], "received_at", nullable=True),
        closed_at=_timestamp(row["closed_at"], "closed_at", nullable=True),
        notes=_text(row["notes"], "notes", 500, nullable=True),
        event_history_json=event_history,
    )


def _item_record(row, transfer):
    item_id = _identifier(row["id"], "id")
    business_id = _identifier(row["business_id"], "business_id")
    transfer_id = _identifier(row["transfer_id"], "transfer_id")
    variant_id = _identifier(row["variant_id"], "variant_id")
    joined_variant_id = row["_joined_variant_id"]
    if (
        business_id != transfer.business_id
        or transfer_id != transfer.id
        or joined_variant_id != variant_id
    ):
        raise ValueError("Stored transfer item does not match its parent.")

    requested_qty = _unsigned_integer(row["requested_qty"], "requested_qty", _UINT32_MAX)
    dispatched_qty = _unsigned_integer(
        row["dispatched_qty"], "dispatched_qty", _UINT32_MAX
    )
    received_qty = _unsigned_integer(row["received_qty"], "received_qty", _UINT32_MAX)
    returned_qty = _unsigned_integer(
        row["returned_to_source_qty"], "returned_to_source_qty", _UINT32_MAX
    )
    written_off_qty = _unsigned_integer(
        row["written_off_qty"], "written_off_qty", _UINT32_MAX
    )
    in_transit_qty = _unsigned_integer(
        row["in_transit_qty"], "in_transit_qty", _UINT32_MAX
    )
    missing_qty = _unsigned_integer(row["missing_qty"], "missing_qty", _UINT32_MAX)
    resolved_qty = _unsigned_integer(row["resolved_qty"], "resolved_qty", _UINT32_MAX)
    validate_transfer_items(
        [{"product_variant_id": variant_id, "quantity": requested_qty}]
    )
    if dispatched_qty > requested_qty:
        raise ValueError("Stored dispatched quantity exceeds requested quantity.")
    if dispatched_qty != received_qty + returned_qty + written_off_qty + in_transit_qty:
        raise ValueError("Stored transfer item quantities do not reconcile.")
    if resolved_qty > missing_qty or missing_qty > dispatched_qty:
        raise ValueError("Stored transfer discrepancy quantities are invalid.")

    discrepancy_reported_by = _identifier(
        row["discrepancy_reported_by"],
        "discrepancy_reported_by",
        nullable=True,
    )
    discrepancy_reported_at = _timestamp(
        row["discrepancy_reported_at"],
        "discrepancy_reported_at",
        nullable=True,
    )
    discrepancy_resolved_at = _timestamp(
        row["discrepancy_resolved_at"],
        "discrepancy_resolved_at",
        nullable=True,
    )
    if missing_qty and (
        discrepancy_reported_by is None or discrepancy_reported_at is None
    ):
        raise ValueError("Stored discrepancy report is incomplete.")
    if discrepancy_resolved_at is not None and not (
        missing_qty > 0 and resolved_qty == missing_qty
    ):
        raise ValueError("Stored discrepancy resolution is inconsistent.")

    return StockTransferItemRecord(
        id=item_id,
        business_id=business_id,
        transfer_id=transfer_id,
        variant_id=variant_id,
        requested_qty=requested_qty,
        dispatched_qty=dispatched_qty,
        received_qty=received_qty,
        returned_to_source_qty=returned_qty,
        written_off_qty=written_off_qty,
        in_transit_qty=in_transit_qty,
        missing_qty=missing_qty,
        resolved_qty=resolved_qty,
        discrepancy_reported_by=discrepancy_reported_by,
        discrepancy_reported_at=discrepancy_reported_at,
        discrepancy_resolved_at=discrepancy_resolved_at,
        discrepancy_notes=_text(
            row["discrepancy_notes"], "discrepancy_notes", nullable=True
        ),
    )


class SQLAlchemyStockTransferRepository:
    """Own fresh SELECT transactions and return immutable detached records.

    The adapter has no standalone view grant. Internal callers must pass the
    operation whose trusted authorization they have already established.
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

    @staticmethod
    def _saved_transfer(session, principal, transfer_id, purpose):
        row = session.execute(
            _transfer_statement(principal, purpose, transfer_id)
        ).mappings().one_or_none()
        if row is None:
            raise RepositoryNotFoundError(_TRANSFER_NOT_FOUND)
        try:
            saved = _transfer_record(row)
        except (KeyError, TypeError, ValueError):
            raise RepositoryPersistenceError(_LOAD_ERROR) from None

        source = ResourceScope(saved.business_id, saved.source_branch_id)
        destination = ResourceScope(saved.business_id, saved.destination_branch_id)
        if not can_access_transfer(
            principal,
            purpose.value,
            source,
            destination,
            permission_branch=purpose.permission_branch,
        ):
            raise RepositoryPersistenceError(_LOAD_ERROR)
        return saved

    def get_stock_transfer(
        self,
        *,
        actor: AuthenticatedSession,
        transfer_id: int,
        purpose: TransferReadPurpose,
    ) -> StockTransferRecord:
        principal = _authorize(actor, purpose)
        _validate_id(transfer_id, "transfer_id")
        try:
            with self._transaction() as session:
                saved = self._saved_transfer(
                    session, principal, transfer_id, purpose
                )
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError(_LOAD_ERROR) from None

    def get_stock_transfer_item(
        self,
        *,
        actor: AuthenticatedSession,
        item_id: int,
        purpose: TransferReadPurpose,
    ) -> StockTransferItemRecord:
        principal = _authorize(actor, purpose)
        _validate_id(item_id, "item_id")
        try:
            with self._transaction() as session:
                row = session.execute(
                    _item_statement(principal, purpose, item_id=item_id)
                ).mappings().one_or_none()
                if row is None:
                    raise RepositoryNotFoundError(_ITEM_NOT_FOUND)
                transfer_id = _identifier(row["transfer_id"], "transfer_id")
                transfer = self._saved_transfer(
                    session, principal, transfer_id, purpose
                )
                try:
                    saved = _item_record(row, transfer)
                except (KeyError, TypeError, ValueError):
                    raise RepositoryPersistenceError(_LOAD_ERROR) from None
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError(_LOAD_ERROR) from None

    def list_stock_transfer_items(
        self,
        *,
        actor: AuthenticatedSession,
        transfer_id: int,
        purpose: TransferReadPurpose,
    ) -> tuple[StockTransferItemRecord, ...]:
        principal = _authorize(actor, purpose)
        _validate_id(transfer_id, "transfer_id")
        try:
            with self._transaction() as session:
                transfer = self._saved_transfer(
                    session, principal, transfer_id, purpose
                )
                rows = session.execute(
                    _item_statement(principal, purpose, transfer_id=transfer_id)
                ).mappings()
                try:
                    saved = tuple(_item_record(row, transfer) for row in rows)
                except (KeyError, TypeError, ValueError):
                    raise RepositoryPersistenceError(_LOAD_ERROR) from None
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError(_LOAD_ERROR) from None
