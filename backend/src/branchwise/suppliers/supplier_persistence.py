"""Internal candidate creation orchestration; no HTTP or application wiring."""
from permissions import ResourceScope
from session_auth import AuthenticatedSession
from branchwise.suppliers.supplier_contracts import (
    SupplierCreateData, SupplierRecord, SupplierRepository, SupplierValidationError,
)
from branchwise.suppliers.supplier_service import prepare_supplier_create


def create_supplier(
    data, *, actor: AuthenticatedSession, scope: ResourceScope,
    repository: SupplierRepository,
) -> SupplierRecord:
    """Authorize before validation/persistence; return only a committed record.

    Actor and scope must be server-established. Session resolution, current
    account/expiry checks and HTTP CSRF remain Developer 1's responsibility.
    The adapter repeats authorization and SQL field checks at its boundary.
    """
    prepared = prepare_supplier_create(data, actor=actor, scope=scope)
    if prepared.errors:
        raise SupplierValidationError(prepared.errors)
    checked = SupplierCreateData(**data)
    return repository.create_supplier_atomic(actor=actor, data=checked)
