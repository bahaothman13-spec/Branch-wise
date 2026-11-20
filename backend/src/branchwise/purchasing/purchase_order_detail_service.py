"""Internal single-order detail using the existing BE-29 read contract.

No history collection permission, SQL, HTTP installation or writes are added.
"""
from dataclasses import dataclass

from isolation_rules import can_access_resource
from permissions import (
    PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED, SUPPLIERS_VIEW,
    ResourceScope, Role, is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryNotFoundError, RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from branchwise.purchasing.purchase_order_contracts import (
    PurchaseOrderItemRecord, PurchaseOrderRecord, PurchaseOrderRepository,
)
from branchwise.suppliers.supplier_contracts import SupplierRecord, SupplierRepository


_LOAD_ERROR = "Purchase order detail could not be loaded."
_NOT_FOUND = "Purchase order not found."


@dataclass(frozen=True)
class PurchaseOrderDetail:
    """Detached persisted records only; costs remain expected order costs."""

    order: PurchaseOrderRecord
    items: tuple[PurchaseOrderItemRecord, ...]
    supplier: SupplierRecord | None


def _authorize(actor):
    if (type(actor) is not AuthenticatedSession
            or not is_positive_integer(actor.user_id)
            or not is_trusted_principal(actor.principal)):
        raise RepositoryAuthorizationError("Permission denied.")
    principal = actor.principal
    permission = (PURCHASES_MANAGE if principal.role is Role.OWNER
                  else PURCHASES_MANAGE_AUTHORIZED)
    if not can_access_resource(principal, permission, ResourceScope(
        principal.business_id, principal.branch_id,
    )):
        raise RepositoryAuthorizationError("Permission denied.")
    return principal, permission


def _matches_id(value, expected):
    return is_positive_integer(value) and value == expected


def get_purchase_order_detail(
    *,
    actor: AuthenticatedSession,
    purchase_order_id: int,
    purchase_order_repository: PurchaseOrderRepository,
    supplier_repository: SupplierRepository | None = None,
) -> PurchaseOrderDetail:
    """Read one order under BE-29 permissions, with optional supplier details.

    The actor must come from SessionManager resolution; expiry and current-user
    checks stay in that layer. Owners need purchases:manage; managers need
    purchases:manage_authorized and their assigned branch; employees are denied.
    No identity or scope fields are accepted separately from the trusted actor.

    Supplying a supplier repository requests enrichment, but only suppliers:view
    at the stored order scope permits its lookup. Otherwise supplier is None,
    with no supplier access or existence check. When requested and permitted,
    a missing or inconsistent supplier fails the complete detail read safely.
    Unenriched reads rely on the approved supplier FK, as BE-29 does.

    Repositories own independent fresh read sessions, not a caller's unit of
    work. Composition promises no shared snapshot or locking. All records are
    checked before returning; existing item order, reference text, status and
    Decimal values are preserved without normalization or arithmetic. Empty
    orders are valid. This does not use the provisional history envelope, whose
    reference name and nonblank rule differ from persisted order detail.
    """
    principal, permission = _authorize(actor)
    if not is_positive_integer(purchase_order_id):
        raise ValueError("purchase_order_id must be a positive integer.")

    order = purchase_order_repository.get_purchase_order(
        actor=actor, purchase_order_id=purchase_order_id,
    )
    if type(order) is not PurchaseOrderRecord:
        raise RepositoryPersistenceError(_LOAD_ERROR)
    scope = ResourceScope(order.business_id, order.branch_id)
    if (not _matches_id(order.purchase_order_id, purchase_order_id)
            or not is_positive_integer(order.branch_id)
            or not can_access_resource(principal, permission, scope)):
        raise RepositoryNotFoundError(_NOT_FOUND)
    if not is_positive_integer(order.supplier_id):
        raise RepositoryPersistenceError(_LOAD_ERROR)

    items = purchase_order_repository.list_purchase_order_items(
        actor=actor, purchase_order_id=order.purchase_order_id,
    )
    if type(items) is not tuple:
        raise RepositoryPersistenceError(_LOAD_ERROR)
    for item in items:
        if (type(item) is not PurchaseOrderItemRecord
                or not _matches_id(item.business_id, order.business_id)
                or not _matches_id(item.purchase_order_id, order.purchase_order_id)
                or not is_positive_integer(item.item_id)
                or not is_positive_integer(item.variant_id)):
            raise RepositoryPersistenceError(_LOAD_ERROR)

    supplier = None
    if (supplier_repository is not None
            and can_access_resource(principal, SUPPLIERS_VIEW, scope)):
        try:
            supplier = supplier_repository.get_supplier(
                actor=actor, supplier_id=order.supplier_id,
            )
        except RepositoryNotFoundError:
            raise RepositoryPersistenceError(_LOAD_ERROR) from None
        if (type(supplier) is not SupplierRecord
                or not _matches_id(supplier.business_id, order.business_id)
                or not _matches_id(supplier.supplier_id, order.supplier_id)):
            raise RepositoryPersistenceError(_LOAD_ERROR)

    return PurchaseOrderDetail(order=order, items=items, supplier=supplier)
