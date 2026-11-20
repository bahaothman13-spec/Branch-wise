"""Candidate SQLAlchemy persistence adapter; deliberately not installed by Flask."""
from contextlib import contextmanager
from dataclasses import asdict
import re
import sqlite3
from typing import Callable

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from isolation_rules import require_resource_access
from permissions import (
    ResourceScope, SUPPLIERS_MANAGE, SUPPLIERS_VIEW, is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryConflictError,
    RepositoryNotFoundError, RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from branchwise.suppliers.supplier_contracts import (
    SupplierCreateData, SupplierRecord, SupplierValidationError,
)
from branchwise.suppliers.supplier_model import suppliers
from branchwise.suppliers.supplier_service import prepare_supplier_create


def _authorize(actor, permission):
    if (type(actor) is not AuthenticatedSession
            or not is_positive_integer(actor.user_id)
            or not is_trusted_principal(actor.principal)):
        raise RepositoryAuthorizationError("Permission denied.")
    scope = ResourceScope(actor.principal.business_id, actor.principal.branch_id)
    try:
        require_resource_access(actor.principal, permission, scope)
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return scope


def _is_duplicate_name(error):
    original = error.orig
    if getattr(original, "errno", None) == 1062:
        return re.search(
            r"\bfor key ['\x60](?:[^'\x60]*\.)?uq_suppliers_name['\x60](?:\s|$)",
            str(original),
        ) is not None
    return (
        getattr(original, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(original) == "UNIQUE constraint failed: suppliers.business_id, suppliers.name"
    )


def _record(row):
    values = dict(row)
    values["supplier_id"] = values.pop("id")
    return SupplierRecord(**values)


class SQLAlchemySupplierRepository:
    """Owns each transaction; inject a factory returning a fresh idle Session.

    For example, sessionmaker(bind=db.engine) inside an application context.
    Never pass the shared scoped db.session or a caller-owned unit of work.
    Core statements avoid an identity-map lookup bypassing tenant predicates.
    Only the supplier table is written. Database defaults own timestamps/status.
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
            # Reject caller-owned work without committing, rolling it back or closing it.
            raise RepositoryPersistenceError("A fresh database session is required.")
        with session:
            with session.begin():
                yield session

    def create_supplier_atomic(
        self, *, actor: AuthenticatedSession, data: SupplierCreateData
    ) -> SupplierRecord:
        scope = _authorize(actor, SUPPLIERS_MANAGE)
        if type(data) is not SupplierCreateData:
            raise TypeError("data must be SupplierCreateData.")
        # Revalidate even if an internal caller bypassed the frozen constructor.
        checked = SupplierCreateData(**asdict(data))
        prepared = prepare_supplier_create(asdict(checked), actor=actor, scope=scope)
        if prepared.errors:
            raise SupplierValidationError(prepared.errors)
        try:
            with self._transaction() as session:
                result = session.execute(insert(suppliers).values(
                    business_id=scope.business_id, **asdict(checked)
                ))
                supplier_id = result.inserted_primary_key[0]
                row = session.execute(select(suppliers).where(
                    suppliers.c.business_id == scope.business_id,
                    suppliers.c.id == supplier_id,
                )).mappings().one()
                saved = _record(row)
            # Includes commit; failed commit must never return success.
            return saved
        except IntegrityError as error:
            if _is_duplicate_name(error):
                raise RepositoryConflictError("Supplier name already exists.") from None
            raise RepositoryPersistenceError("Supplier could not be saved.") from None
        except SQLAlchemyError:
            raise RepositoryPersistenceError("Supplier could not be saved.") from None

    def get_supplier(
        self, *, actor: AuthenticatedSession, supplier_id: int
    ) -> SupplierRecord:
        scope = _authorize(actor, SUPPLIERS_VIEW)
        if not is_positive_integer(supplier_id):
            raise ValueError("supplier_id must be a positive integer.")
        try:
            with self._transaction() as session:
                row = session.execute(select(suppliers).where(
                    suppliers.c.business_id == scope.business_id,
                    suppliers.c.id == supplier_id,
                )).mappings().one_or_none()
                if row is None:
                    raise RepositoryNotFoundError("Supplier not found.")
                saved = _record(row)
            return saved
        except SQLAlchemyError:
            raise RepositoryPersistenceError("Supplier could not be loaded.") from None
