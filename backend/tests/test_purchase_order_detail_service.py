"""BE-31 single-order detail; disposable SQLite, never live MySQL verification."""
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, Inexact, Rounded, localcontext
import socket
from unittest.mock import Mock

import pytest
from sqlalchemy import event, insert, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, registry

from permissions import (
    AuthenticatedPrincipal, PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED,
    SUPPLIERS_VIEW, Role,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryNotFoundError, RepositoryPersistenceError,
)
from branchwise.purchasing.purchase_order_contracts import PurchaseOrderRepository
from branchwise.purchasing.purchase_order_detail_service import (
    PurchaseOrderDetail, get_purchase_order_detail,
)
from branchwise.purchasing.purchase_order_model import purchase_orders
from branchwise.purchasing.purchase_order_repository import SQLAlchemyPurchaseOrderRepository
from branchwise.suppliers.supplier_contracts import SupplierRepository
from branchwise.suppliers.supplier_repository import SQLAlchemySupplierRepository

# Reuse BE-29's private copied metadata, FK-enabled disposable SQLite fixtures,
# parent/inventory sentinels and snapshot helper. No production DDL is executed.
from test_purchase_order_repository import actor, database, seeded, snapshot


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempted = Mock(side_effect=AssertionError("Network access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted)
    monkeypatch.setattr(socket, "getaddrinfo", attempted)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, attempted)
    yield
    attempted.assert_not_called()


def manager(actor):
    return replace(actor, principal=AuthenticatedPrincipal(
        Role.BRANCH_MANAGER, 10, 2, frozenset({PURCHASES_MANAGE_AUTHORIZED}),
    ))


def with_supplier_grant(actor):
    return replace(actor, principal=replace(
        actor.principal, permissions=actor.principal.permissions | {SUPPLIERS_VIEW},
    ))


def detail(actor, database, *, order_id=1, enrich=False):
    return get_purchase_order_detail(
        actor=actor, purchase_order_id=order_id,
        purchase_order_repository=SQLAlchemyPurchaseOrderRepository(database[1]),
        supplier_repository=SQLAlchemySupplierRepository(database[1]) if enrich else None,
    )


def test_owner_reads_stored_header_and_multiple_lines(actor, seeded):
    result = detail(actor, seeded)
    assert type(result) is PurchaseOrderDetail
    assert result.order.business_id == 10 and result.order.branch_id == 2
    assert result.order.purchase_order_id == 1
    assert result.order.purchase_reference == "APPROVED-TEST-1"
    assert result.order.expected_total_cost == Decimal("6.72")
    assert [item.item_id for item in result.items] == [1, 4]
    assert all(item.business_id == 10 and item.purchase_order_id == 1 for item in result.items)
    assert result.supplier is None


def test_manager_assigned_branch_and_owner_other_branch(actor, seeded):
    assert detail(manager(actor), seeded).order.branch_id == 2
    assert detail(actor, seeded, order_id=3).order.branch_id == 7


@pytest.mark.parametrize("role,order_id", [
    (Role.OWNER, 2), (Role.OWNER, 999),
    (Role.BRANCH_MANAGER, 2), (Role.BRANCH_MANAGER, 3), (Role.BRANCH_MANAGER, 999),
])
def test_missing_foreign_business_and_other_branch_are_indistinguishable(actor, seeded, role, order_id):
    actor = with_supplier_grant(manager(actor) if role is Role.BRANCH_MANAGER else actor)
    orders = Mock(wraps=SQLAlchemyPurchaseOrderRepository(seeded[1]))
    suppliers = Mock(spec=SupplierRepository)
    with pytest.raises(RepositoryNotFoundError, match="^Purchase order not found[.]$"):
        get_purchase_order_detail(
            actor=actor, purchase_order_id=order_id,
            purchase_order_repository=orders, supplier_repository=suppliers,
        )
    orders.list_purchase_order_items.assert_not_called()
    suppliers.get_supplier.assert_not_called()


def test_accessible_order_with_zero_lines(actor, seeded):
    result = detail(actor, seeded, order_id=4)
    assert result.order.purchase_order_id == 4 and result.items == ()


@pytest.mark.parametrize("kind", [
    "none", "request-metadata", "bad-user", "bad-business", "bad-role",
    "owner-no-grant", "owner-wrong-grant", "owner-assigned-branch",
    "manager-no-grant", "manager-wrong-grant", "manager-no-branch", "employee",
])
def test_authorization_precedes_all_repository_calls_and_id_validation(actor, kind):
    principal = actor.principal
    denied = {
        "none": None,
        "request-metadata": {"user_id": 1, "principal": principal, "authorized": True},
        "bad-user": replace(actor, user_id=True),
        "bad-business": replace(actor, principal=replace(principal, business_id=True)),
        "bad-role": replace(actor, principal=replace(principal, role="Owner")),
        "owner-no-grant": replace(actor, principal=replace(principal, permissions=frozenset())),
        "owner-wrong-grant": replace(actor, principal=replace(
            principal, permissions=frozenset({PURCHASES_MANAGE_AUTHORIZED, SUPPLIERS_VIEW}))),
        "owner-assigned-branch": replace(actor, principal=replace(principal, branch_id=2)),
        "manager-no-grant": replace(manager(actor), principal=replace(
            manager(actor).principal, permissions=frozenset({SUPPLIERS_VIEW}))),
        "manager-wrong-grant": replace(manager(actor), principal=replace(
            manager(actor).principal, permissions=frozenset({PURCHASES_MANAGE}))),
        "manager-no-branch": replace(manager(actor), principal=replace(
            manager(actor).principal, branch_id=None)),
        "employee": replace(actor, principal=AuthenticatedPrincipal(
            Role.EMPLOYEE, 10, 2,
            frozenset({PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED, SUPPLIERS_VIEW}))),
    }[kind]
    orders, suppliers = Mock(spec=PurchaseOrderRepository), Mock(spec=SupplierRepository)
    with pytest.raises(RepositoryAuthorizationError, match="^Permission denied[.]$"):
        get_purchase_order_detail(
            actor=denied, purchase_order_id=None,
            purchase_order_repository=orders, supplier_repository=suppliers,
        )
    assert orders.mock_calls == suppliers.mock_calls == []


@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, "1", 1.0, Decimal("1"), [], {}])
def test_invalid_order_ids_do_not_access_repositories(actor, invalid):
    orders, suppliers = Mock(spec=PurchaseOrderRepository), Mock(spec=SupplierRepository)
    with pytest.raises(ValueError, match="positive integer"):
        get_purchase_order_detail(
            actor=actor, purchase_order_id=invalid,
            purchase_order_repository=orders, supplier_repository=suppliers,
        )
    assert orders.mock_calls == suppliers.mock_calls == []


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_supplier_enrichment_uses_separate_grant_and_exact_trusted_actor(actor, seeded, role):
    actor = with_supplier_grant(manager(actor) if role is Role.BRANCH_MANAGER else actor)
    orders = Mock(wraps=SQLAlchemyPurchaseOrderRepository(seeded[1]))
    suppliers = Mock(wraps=SQLAlchemySupplierRepository(seeded[1]))
    result = get_purchase_order_detail(
        actor=actor, purchase_order_id=1,
        purchase_order_repository=orders, supplier_repository=suppliers,
    )
    assert result.supplier.supplier_id == result.order.supplier_id == 1
    assert result.supplier.business_id == result.order.business_id == 10
    assert result.supplier.name == "Supplier"
    orders.get_purchase_order.assert_called_once_with(actor=actor, purchase_order_id=1)
    orders.list_purchase_order_items.assert_called_once_with(actor=actor, purchase_order_id=1)
    suppliers.get_supplier.assert_called_once_with(actor=actor, supplier_id=1)


@pytest.mark.parametrize("role", [Role.OWNER, Role.BRANCH_MANAGER])
def test_missing_supplier_permission_skips_lookup_and_details(actor, seeded, role):
    actor = manager(actor) if role is Role.BRANCH_MANAGER else actor
    suppliers = Mock(spec=SupplierRepository)
    result = get_purchase_order_detail(
        actor=actor, purchase_order_id=1,
        purchase_order_repository=SQLAlchemyPurchaseOrderRepository(seeded[1]),
        supplier_repository=suppliers,
    )
    assert result.supplier is None
    assert result.order.supplier_id == 1  # Existing approved order-header field.
    suppliers.get_supplier.assert_not_called()


def test_supplier_repository_is_optional_even_with_grant(actor, seeded):
    assert detail(with_supplier_grant(actor), seeded).supplier is None


@pytest.mark.parametrize("supplier_id", [91, 999])
def test_foreign_or_missing_supplier_cannot_be_enriched(actor, seeded, supplier_id):
    # Break only this disposable fixture to exercise required enrichment checks.
    with seeded[0].connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(update(purchase_orders).where(purchase_orders.c.id == 1).values(
            supplier_id=supplier_id))
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    before = snapshot(seeded)
    with pytest.raises(RepositoryPersistenceError) as caught:
        detail(with_supplier_grant(actor), seeded, enrich=True)
    assert str(caught.value) == "Purchase order detail could not be loaded."
    assert caught.value.__suppress_context__
    assert snapshot(seeded) == before


@pytest.mark.parametrize("role,changes", [
    (Role.BRANCH_MANAGER, {"business_id": 99}),
    (Role.BRANCH_MANAGER, {"business_id": True}),
    (Role.BRANCH_MANAGER, {"branch_id": 7}),
    (Role.BRANCH_MANAGER, {"purchase_order_id": 2}),
    (Role.BRANCH_MANAGER, {"purchase_order_id": True}),
    (Role.OWNER, {"branch_id": None}),
])
def test_unscoped_header_from_adapter_cannot_escape_or_load_dependencies(actor, seeded, role, changes):
    order = detail(actor, seeded).order
    orders = Mock(spec=PurchaseOrderRepository)
    orders.get_purchase_order.return_value = replace(order, **changes)
    suppliers = Mock(spec=SupplierRepository)
    with pytest.raises(RepositoryNotFoundError, match="^Purchase order not found[.]$"):
        get_purchase_order_detail(
            actor=with_supplier_grant(manager(actor) if role is Role.BRANCH_MANAGER else actor),
            purchase_order_id=1,
            purchase_order_repository=orders, supplier_repository=suppliers,
        )
    orders.list_purchase_order_items.assert_not_called()
    suppliers.get_supplier.assert_not_called()


@pytest.mark.parametrize("kind", ["missing-header", "missing-supplier-id", "mutable-items",
                                     "wrong-business", "wrong-order", "bad-line-type", "bad-line-id"])
def test_inconsistent_required_records_never_return_partial_detail(actor, seeded, kind):
    stored = detail(actor, seeded)
    orders = Mock(spec=PurchaseOrderRepository)
    orders.get_purchase_order.return_value = stored.order
    orders.list_purchase_order_items.return_value = stored.items
    if kind == "missing-header":
        orders.get_purchase_order.return_value = None
    elif kind == "missing-supplier-id":
        orders.get_purchase_order.return_value = replace(stored.order, supplier_id=None)
    elif kind == "mutable-items":
        orders.list_purchase_order_items.return_value = list(stored.items)
    else:
        bad = {
            "wrong-business": replace(stored.items[1], business_id=99),
            "wrong-order": replace(stored.items[1], purchase_order_id=3),
            "bad-line-type": {"business_id": 10, "purchase_order_id": 1},
            "bad-line-id": replace(stored.items[1], item_id=True),
        }[kind]
        orders.list_purchase_order_items.return_value = (stored.items[0], bad)
    suppliers = Mock(spec=SupplierRepository)
    with pytest.raises(RepositoryPersistenceError, match="^Purchase order detail could not be loaded[.]$"):
        get_purchase_order_detail(
            actor=with_supplier_grant(actor), purchase_order_id=1,
            purchase_order_repository=orders, supplier_repository=suppliers,
        )
    suppliers.get_supplier.assert_not_called()


@pytest.mark.parametrize("kind", ["wrong-business", "wrong-supplier", "wrong-type"])
def test_inconsistent_supplier_record_does_not_escape(actor, seeded, kind):
    actor = with_supplier_grant(actor)
    supplier = detail(actor, seeded, enrich=True).supplier
    suppliers = Mock(spec=SupplierRepository)
    suppliers.get_supplier.return_value = {
        "wrong-business": replace(supplier, business_id=99),
        "wrong-supplier": replace(supplier, supplier_id=91),
        "wrong-type": {"supplier_id": 1, "business_id": 10},
    }[kind]
    with pytest.raises(RepositoryPersistenceError, match="^Purchase order detail could not be loaded[.]$"):
        get_purchase_order_detail(
            actor=actor, purchase_order_id=1,
            purchase_order_repository=SQLAlchemyPurchaseOrderRepository(seeded[1]),
            supplier_repository=suppliers,
        )


@pytest.mark.parametrize("reference", ["  PO/Test-001  ", "", " "])
@pytest.mark.parametrize("status", ["PENDING", "PARTIALLY_RECEIVED", "RECEIVED"])
def test_stored_reference_and_status_are_not_normalized_or_reinterpreted(actor, seeded, reference, status):
    with seeded[1].begin() as session:
        session.execute(update(purchase_orders).where(purchase_orders.c.id == 1).values(
            purchase_reference=reference, status=status))
    result = detail(actor, seeded)
    assert result.order.purchase_reference == reference and result.order.status == status


@pytest.mark.parametrize("amount", ["0.00", "0.01", "0.10", "999999999999.99"])
def test_exact_stored_decimals_without_recomputing_header_total(actor, seeded, amount):
    with seeded[1].begin() as session:
        session.execute(update(purchase_orders).where(purchase_orders.c.id == 1).values(
            expected_total_cost=Decimal(amount)))
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        context.clear_flags()
        result = detail(actor, seeded)
        assert result.order.expected_total_cost.as_tuple() == Decimal(amount).as_tuple()
        assert result.items[0].unit_cost.as_tuple() == Decimal("0.10").as_tuple()
        assert result.items[0].line_total.as_tuple() == Decimal("0.30").as_tuple()
        assert result.items[1].unit_cost.as_tuple() == Decimal("3.21").as_tuple()
        assert result.items[1].line_total.as_tuple() == Decimal("6.42").as_tuple()
        assert not any(context.flags.values())


def test_immutable_detached_result_and_source_records(actor, seeded):
    before = snapshot(seeded)
    first = detail(with_supplier_grant(actor), seeded, enrich=True)
    second = detail(with_supplier_grant(actor), seeded, enrich=True)
    assert first == second and first is not second
    assert {field.name for field in fields(first)} == {"order", "items", "supplier"}
    assert type(first.items) is tuple
    for record, field, value in (
        (first, "supplier", None), (first.order, "purchase_reference", "changed"),
        (first.items[0], "ordered_qty", 99), (first.supplier, "name", "changed"),
    ):
        assert not hasattr(record, "_sa_instance_state")
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)
    assert second == detail(with_supplier_grant(actor), seeded, enrich=True)
    assert snapshot(seeded) == before


def test_independent_closed_sessions_select_only_and_unchanged_database(actor, seeded):
    sessions, statements = [], []
    before = snapshot(seeded)

    class TrackingSession(Session):
        def close(self):
            super().close()
            self.info["closed"] = True

    def factory():
        session = TrackingSession(bind=seeded[0])
        sessions.append(session)
        return session

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(seeded[0], "before_cursor_execute", observe)
    try:
        result = get_purchase_order_detail(
            actor=with_supplier_grant(actor), purchase_order_id=1,
            purchase_order_repository=SQLAlchemyPurchaseOrderRepository(factory),
            supplier_repository=SQLAlchemySupplierRepository(factory),
        )
    finally:
        event.remove(seeded[0], "before_cursor_execute", observe)
    assert result.supplier is not None
    assert len(sessions) == len({id(session) for session in sessions}) == 3
    assert all(session.info.get("closed") and not session.in_transaction() for session in sessions)
    assert statements and all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)
    assert snapshot(seeded) == before


@pytest.mark.parametrize("stage", ["order", "supplier"])
@pytest.mark.parametrize("state", ["active", "pending", "dirty", "deleted"])
def test_caller_owned_work_is_not_flushed_committed_rolled_back_or_closed(actor, seeded, stage, state):
    probe = seeded[2].tables["inventory_probe"]
    mapper_registry = registry()

    class Probe:
        pass

    mapper_registry.map_imperatively(Probe, probe, primary_key=[probe.c.value])
    borrowed = Session(bind=seeded[0])
    before = snapshot(seeded)
    mutations = Mock()
    for name in ("before_flush", "after_commit", "after_rollback"):
        event.listen(borrowed, name, mutations)
    try:
        if state == "active":
            borrowed.execute(insert(probe).values(value="caller work"))
        elif state == "pending":
            pending = Probe()
            pending.value = "caller pending"
            borrowed.add(pending)
        elif state == "dirty":
            borrowed.get(Probe, "unchanged").value = "caller dirty"
        else:
            borrowed.delete(borrowed.get(Probe, "unchanged"))
        original_state = (set(borrowed.new), set(borrowed.dirty), set(borrowed.deleted))
        original_transaction = borrowed.get_transaction()
        orders = SQLAlchemyPurchaseOrderRepository(lambda: borrowed if stage == "order" else seeded[1]())
        suppliers = SQLAlchemySupplierRepository(lambda: borrowed)
        with pytest.raises(RepositoryPersistenceError, match="fresh database session"):
            get_purchase_order_detail(
                actor=with_supplier_grant(actor), purchase_order_id=1,
                purchase_order_repository=orders, supplier_repository=suppliers,
            )
        assert borrowed.get_transaction() is original_transaction
        assert original_transaction.is_active
        assert (set(borrowed.new), set(borrowed.dirty), set(borrowed.deleted)) == original_state
        mutations.assert_not_called()
    finally:
        borrowed.rollback()
        borrowed.close()
        mapper_registry.dispose()
    assert snapshot(seeded) == before


@pytest.mark.parametrize("failing_read", [2, 3])
def test_later_database_failure_returns_no_partial_detail_or_private_error(actor, seeded, failing_read):
    before = snapshot(seeded)
    calls = []

    def factory():
        calls.append(True)
        if len(calls) == failing_read:
            raise OperationalError("private SQL and credentials", {}, Exception("secret"))
        return seeded[1]()

    with pytest.raises(RepositoryPersistenceError) as caught:
        get_purchase_order_detail(
            actor=with_supplier_grant(actor), purchase_order_id=1,
            purchase_order_repository=SQLAlchemyPurchaseOrderRepository(factory),
            supplier_repository=SQLAlchemySupplierRepository(factory),
        )
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert snapshot(seeded) == before
