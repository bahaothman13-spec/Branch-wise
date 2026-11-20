"""BE-30 read integration tests against disposable SQLite only.

MySQL DDL compilation is static coverage. Live MySQL unsigned ranges,
VARCHAR/collation, DATETIME(6), CHECK/FK and transaction behavior are unverified.
Test-only parent stubs and portable timestamp defaults never alter db.metadata.
"""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import (
    Column, DefaultClause, ForeignKeyConstraint, Integer, MetaData, Table, Text,
    UniqueConstraint, create_engine, event, func, insert, select, text, update,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, registry, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app import create_app
from db import db
from permissions import (
    PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED, AuthenticatedPrincipal,
    ResourceScope, Role,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryNotFoundError, RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession, SESSION_AUTH_EXTENSION
from branchwise.purchasing.purchase_order_model import purchase_order_items, purchase_orders
from branchwise.purchasing.purchase_receiving_contracts import (
    PurchaseReceiptItemRecord, PurchaseReceiptRecord, PurchaseReceivingRepository,
)
from branchwise.purchasing.purchase_receiving_model import purchase_receipt_items, purchase_receipts
from branchwise.purchasing.purchase_receiving_repository import SQLAlchemyPurchaseReceivingRepository
from branchwise.purchasing.purchase_receiving_service import prepare_purchase_receiving_input
from branchwise.suppliers.supplier_model import suppliers


OPERATIONS = ("receipt", "item", "list")
INVALID_IDS = (None, True, False, 0, -1, "1", 1.0, Decimal("1"), [], {})


@pytest.fixture
def actor():
    return AuthenticatedSession(
        1, 0, AuthenticatedPrincipal(Role.OWNER, 10, None, frozenset({PURCHASES_MANAGE})),
        datetime(2030, 1, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64,
    )


def _metadata():
    metadata = MetaData()
    Table("businesses", metadata, Column("id", Integer, primary_key=True))
    for name in ("branches", "users", "product_variants"):
        Table(name, metadata, Column("id", Integer, primary_key=True),
              Column("business_id", Integer, nullable=False), UniqueConstraint("business_id", "id"))
    for source in (suppliers, purchase_orders, purchase_order_items,
                   purchase_receipts, purchase_receipt_items):
        copied = source.to_metadata(metadata)
        for name in ("ordered_at", "created_at", "updated_at", "received_at"):
            if name in copied.c:
                copied.c[name].server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    Table("inventory_probe", metadata, Column("value", Text, primary_key=True))
    return metadata


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "purchase_receiving.sqlite").as_posix())

    @event.listens_for(engine, "connect")
    def setup(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("CHAR_LENGTH", 1, len, deterministic=True)

    metadata = _metadata()
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        session.execute(insert(metadata.tables["businesses"]), [{"id": 10}, {"id": 99}])
        for name, values in (
            ("branches", [(2, 10), (7, 10), (92, 99)]),
            ("users", [(1, 10), (91, 99)]),
            ("product_variants", [(3, 10), (4, 10), (93, 99)]),
        ):
            session.execute(insert(metadata.tables[name]),
                            [{"id": key, "business_id": business} for key, business in values])
        # All inserts use copied test tables with portable defaults.
        session.execute(insert(metadata.tables["suppliers"]), [
            {"id": 1, "business_id": 10, "name": "Supplier"},
            {"id": 91, "business_id": 99, "name": "Foreign supplier"},
        ])
        session.execute(insert(metadata.tables["inventory_probe"]).values(value="unchanged"))
        session.execute(insert(metadata.tables["purchase_orders"]), [
            _order(id=1),
            _order(id=2, business_id=99, branch_id=92, supplier_id=91, ordered_by_user_id=91),
            _order(id=3, branch_id=7, purchase_reference="OTHER-BRANCH"),
            _order(id=4, purchase_reference="SAME-BRANCH-OTHER-ORDER"),
        ])
        session.execute(insert(purchase_order_items), [
            _order_item(id=1),
            _order_item(id=2, business_id=99, purchase_order_id=2, variant_id=93),
            _order_item(id=3, purchase_order_id=3),
            _order_item(id=4, variant_id=4),
            _order_item(id=5, purchase_order_id=4),
        ])
    try:
        yield engine, factory, metadata
    finally:
        engine.dispose()


def _order(**changes):
    return dict({"business_id": 10, "branch_id": 2, "supplier_id": 1,
                 "purchase_reference": "PO-1", "ordered_by_user_id": 1,
                 "expected_total_cost": Decimal("1.00")}, **changes)


def _order_item(**changes):
    return dict({"business_id": 10, "purchase_order_id": 1, "variant_id": 3,
                 "ordered_qty": 10, "unit_cost": Decimal("0.10"),
                 "line_total": Decimal("1.00")}, **changes)


def _receipt(**changes):
    return dict({"business_id": 10, "purchase_order_id": 1,
                 "receipt_reference": "RECEIPT-1", "request_key": "REQUEST-1",
                 "received_by_user_id": 1, "notes": None}, **changes)


def _item(**changes):
    return dict({"business_id": 10, "purchase_receipt_id": 1,
                 "purchase_order_item_id": 1, "received_qty": 2}, **changes)


@pytest.fixture
def seeded(database):
    with database[1].begin() as session:
        session.execute(insert(database[2].tables["purchase_receipts"]), [
            _receipt(id=1, notes="Checked"),
            _receipt(id=2, business_id=99, purchase_order_id=2, received_by_user_id=91),
            _receipt(id=3, purchase_order_id=3, receipt_reference="OTHER-BRANCH", request_key="OTHER"),
            _receipt(id=4, receipt_reference="EMPTY", request_key="EMPTY"),
        ])
        session.execute(update(purchase_receipts).where(purchase_receipts.c.id == 1).values(
            received_at=datetime(2026, 11, 20, 12, 13, 14, 123456)))
        session.execute(insert(purchase_receipt_items), [
            _item(id=1),
            _item(id=2, business_id=99, purchase_receipt_id=2, purchase_order_item_id=2),
            _item(id=3, purchase_receipt_id=3, purchase_order_item_id=3),
            _item(id=4, purchase_order_item_id=4, received_qty=1),
        ])
    return database


def lookup(repository, operation, actor, *, receipt_id=1, item_id=1):
    if operation == "receipt":
        return repository.get_purchase_receipt(actor=actor, receipt_id=receipt_id)
    if operation == "item":
        return repository.get_purchase_receipt_item(actor=actor, receipt_id=receipt_id, item_id=item_id)
    return repository.list_purchase_receipt_items(actor=actor, receipt_id=receipt_id)


def snapshot(database):
    with database[1]() as session:
        return {name: session.execute(select(table)).all()
                for name, table in database[2].tables.items()}


def test_exact_approved_mappings_and_static_mysql_ddl():
    metadata = _metadata()
    expected = (
        (purchase_receipts,
         {"id", "business_id", "purchase_order_id", "receipt_reference", "request_key",
          "received_by_user_id", "received_at", "notes"},
         {"uq_purchase_receipts_bid_id", "uq_purchase_receipts_reference",
          "uq_purchase_receipts_request", "fk_purchase_receipts_business_id",
          "fk_purchase_receipts_purchase_order_id", "fk_purchase_receipts_received_by_user_id"}),
        (purchase_receipt_items,
         {"id", "business_id", "purchase_receipt_id", "purchase_order_item_id", "received_qty"},
         {"uq_purchase_receipt_items_bid_id", "uq_purchase_receipt_items_line",
          "ck_purchase_receipt_items_qty", "fk_purchase_receipt_items_business_id",
          "fk_purchase_receipt_items_purchase_receipt_id",
          "fk_purchase_receipt_items_purchase_order_item_id"}),
    )
    for table, columns, constraints in expected:
        assert table.metadata is db.metadata
        assert set(table.c.keys()) == columns
        assert tuple(table.primary_key.columns.keys()) == ("id",)
        assert table.c.id.autoincrement is True
        assert {c.name for c in table.constraints if c.name} == constraints
        for column in table.c:
            assert column.nullable is (column.name == "notes")
            if column.name == "id" or column.name.endswith("_id"):
                assert str(column.type.compile(dialect=mysql.dialect())) == "BIGINT UNSIGNED"
            assert column.default is None
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                assert constraint.ondelete is None and constraint.onupdate is None
        ddl = str(CreateTable(metadata.tables[table.name]).compile(dialect=mysql.dialect()))
        for fragment in ("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT", "ENGINE=InnoDB",
                         "CHARSET=utf8mb4", "COLLATE utf8mb4_0900_ai_ci", "UNIQUE (business_id, id)",
                         "FOREIGN KEY(business_id) REFERENCES businesses (id)"):
            assert fragment in ddl
    copied = metadata.tables["purchase_receipts"]
    copied.c.received_at.server_default = purchase_receipts.c.received_at.server_default
    ddl = str(CreateTable(copied).compile(dialect=mysql.dialect()))
    for fragment in (
        "receipt_reference VARCHAR(40) NOT NULL", "request_key VARCHAR(120) NOT NULL",
        "notes VARCHAR(500)", "received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "UNIQUE (business_id, receipt_reference)", "UNIQUE (business_id, request_key)",
        "FOREIGN KEY(business_id, purchase_order_id) REFERENCES purchase_orders (business_id, id)",
        "FOREIGN KEY(business_id, received_by_user_id) REFERENCES users (business_id, id)",
    ):
        assert fragment in ddl
    ddl = str(CreateTable(metadata.tables["purchase_receipt_items"]).compile(dialect=mysql.dialect()))
    for fragment in (
        "received_qty INTEGER UNSIGNED NOT NULL", "CHECK (received_qty > 0)",
        "UNIQUE (purchase_receipt_id, purchase_order_item_id)",
        "FOREIGN KEY(business_id, purchase_receipt_id) REFERENCES purchase_receipts (business_id, id)",
        "FOREIGN KEY(business_id, purchase_order_item_id) REFERENCES purchase_order_items (business_id, id)",
    ):
        assert fragment in ddl
    assert {i.name: tuple(i.columns.keys()) for i in purchase_receipts.indexes} == {
        "ix_purchase_receipts_1": ("business_id", "purchase_order_id", "received_at"),
    }
    assert not purchase_receipt_items.indexes
    assert str(CreateIndex(next(iter(purchase_receipts.indexes))).compile(dialect=mysql.dialect())) == (
        "CREATE INDEX ix_purchase_receipts_1 ON purchase_receipts (business_id, purchase_order_id, received_at)"
    )
    assert db.metadata.tables["purchase_orders"] is purchase_orders
    assert db.metadata.tables["purchase_order_items"] is purchase_order_items
    assert purchase_receipts.c.receipt_reference.type.length == 40
    assert purchase_receipts.c.request_key.type.length == 120
    assert purchase_receipts.c.notes.type.length == 500
    assert purchase_receipts.c.received_at.server_onupdate is None


def test_detached_records_and_destination_across_independent_sessions(actor, seeded):
    sessions = []
    class TrackingSession(Session):
        def close(self):
            super().close()
            self.info["closed"] = True
    def factory():
        session = TrackingSession(bind=seeded[0])
        sessions.append(session)
        return session
    repository: PurchaseReceivingRepository = SQLAlchemyPurchaseReceivingRepository(factory)
    receipt = repository.get_purchase_receipt(actor=actor, receipt_id=1)
    item = repository.get_purchase_receipt_item(actor=actor, receipt_id=1, item_id=1)
    items = repository.list_purchase_receipt_items(actor=actor, receipt_id=1)
    assert type(receipt) is PurchaseReceiptRecord and type(item) is PurchaseReceiptItemRecord
    assert (receipt.receipt_id, receipt.business_id, receipt.purchase_order_id, receipt.branch_id) == (1, 10, 1, 2)
    assert receipt.receipt_reference == "RECEIPT-1" and receipt.request_key == "REQUEST-1"
    assert receipt.received_by_user_id == 1 and receipt.notes == "Checked"
    assert receipt.received_at == datetime(2026, 11, 20, 12, 13, 14, 123456)
    assert (item.item_id, item.business_id, item.purchase_receipt_id, item.purchase_order_item_id,
            item.received_qty) == (1, 10, 1, 1, 2)
    assert type(items) is tuple and [line.item_id for line in items] == [1, 4]
    assert items[0] == item
    assert len({id(session) for session in sessions}) == 3
    assert all(session.info["closed"] and not session.in_transaction() for session in sessions)
    for record in (receipt, item):
        assert not hasattr(record, "_sa_instance_state")
        with pytest.raises(FrozenInstanceError):
            record.business_id = 99
    with seeded[1]() as independent:
        assert independent.scalar(select(purchase_orders.c.branch_id).where(
            purchase_orders.c.id == receipt.purchase_order_id)) == receipt.branch_id
        assert independent.scalar(select(purchase_receipts.c.received_at).where(
            purchase_receipts.c.id == receipt.receipt_id)) == receipt.received_at


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("receipt_id,item_id", [(2, 2), (999, 999)])
def test_foreign_business_and_missing_receipts_are_indistinguishable(actor, seeded, operation, receipt_id, item_id):
    with pytest.raises(RepositoryNotFoundError, match="^Purchase receipt not found.$"):
        lookup(SQLAlchemyPurchaseReceivingRepository(seeded[1]), operation, actor,
               receipt_id=receipt_id, item_id=item_id)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_manager_destination_scope_and_owner_cross_branch_access(actor, seeded, operation):
    manager = replace(actor, principal=AuthenticatedPrincipal(
        Role.BRANCH_MANAGER, 10, 2, frozenset({PURCHASES_MANAGE_AUTHORIZED})))
    repository = SQLAlchemyPurchaseReceivingRepository(seeded[1])
    assert lookup(repository, operation, manager)
    with pytest.raises(RepositoryNotFoundError, match="^Purchase receipt not found.$"):
        lookup(repository, operation, manager, receipt_id=3, item_id=3)
    assert lookup(repository, operation, actor, receipt_id=3, item_id=3)
    foreign = replace(actor, principal=replace(actor.principal, business_id=99))
    assert lookup(repository, operation, foreign, receipt_id=2, item_id=2)
    with pytest.raises(RepositoryNotFoundError):
        lookup(repository, operation, foreign)


@pytest.mark.parametrize("receipt_id,item_id", [(1, 2), (1, 3), (3, 1), (4, 1), (1, 999)])
def test_item_must_belong_to_requested_receipt(actor, seeded, receipt_id, item_id):
    with pytest.raises(RepositoryNotFoundError, match="^Purchase receipt item not found.$"):
        lookup(SQLAlchemyPurchaseReceivingRepository(seeded[1]), "item", actor,
               receipt_id=receipt_id, item_id=item_id)


def test_existing_empty_receipt_returns_empty_tuple_and_nullable_notes(actor, seeded):
    repository = SQLAlchemyPurchaseReceivingRepository(seeded[1])
    assert repository.list_purchase_receipt_items(actor=actor, receipt_id=4) == ()
    assert repository.get_purchase_receipt(actor=actor, receipt_id=4).notes is None


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("kind", ["none", "mapping", "bad-user", "bad-business", "bad-role",
                                 "owner-no-grant", "owner-wrong-grant", "manager-no-grant",
                                 "manager-wrong-grant", "manager-no-branch", "employee"])
def test_unauthorized_access_fails_before_session_even_for_bad_ids(actor, operation, kind):
    principal = actor.principal
    denied = {
        "none": None, "mapping": {}, "bad-user": replace(actor, user_id=True),
        "bad-business": replace(actor, principal=replace(principal, business_id=True)),
        "bad-role": replace(actor, principal=replace(principal, role="Owner")),
        "owner-no-grant": replace(actor, principal=replace(principal, permissions=frozenset())),
        "owner-wrong-grant": replace(actor, principal=replace(
            principal, permissions=frozenset({PURCHASES_MANAGE_AUTHORIZED}))),
        "manager-no-grant": replace(actor, principal=AuthenticatedPrincipal(
            Role.BRANCH_MANAGER, 10, 2, frozenset())),
        "manager-wrong-grant": replace(actor, principal=AuthenticatedPrincipal(
            Role.BRANCH_MANAGER, 10, 2, frozenset({PURCHASES_MANAGE}))),
        "manager-no-branch": replace(actor, principal=AuthenticatedPrincipal(
            Role.BRANCH_MANAGER, 10, None, frozenset({PURCHASES_MANAGE_AUTHORIZED}))),
        "employee": replace(actor, principal=AuthenticatedPrincipal(
            Role.EMPLOYEE, 10, 2, frozenset({PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED}))),
    }[kind]
    factory = Mock(side_effect=AssertionError("Unauthorized persistence attempted"))
    with pytest.raises(RepositoryAuthorizationError, match="^Permission denied.$"):
        lookup(SQLAlchemyPurchaseReceivingRepository(factory), operation, denied, receipt_id=None)
    factory.assert_not_called()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("invalid", INVALID_IDS)
def test_malformed_receipt_ids_fail_before_session(actor, operation, invalid):
    factory = Mock()
    with pytest.raises(ValueError, match="receipt_id must be a positive integer"):
        lookup(SQLAlchemyPurchaseReceivingRepository(factory), operation, actor, receipt_id=invalid)
    factory.assert_not_called()


@pytest.mark.parametrize("invalid", INVALID_IDS)
def test_malformed_item_ids_fail_before_session(actor, invalid):
    factory = Mock()
    with pytest.raises(ValueError, match="item_id must be a positive integer"):
        lookup(SQLAlchemyPurchaseReceivingRepository(factory), "item", actor, item_id=invalid)
    factory.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"business_id": 1234}, {"purchase_order_id": 999}, {"purchase_order_id": 2},
    {"received_by_user_id": 999}, {"received_by_user_id": 91},
    {"purchase_order_id": None}, {"business_id": None}, {"receipt_reference": None},
    {"request_key": None}, {"received_by_user_id": None}, {"received_at": None},
    {"receipt_reference": "RECEIPT-1"}, {"request_key": "REQUEST-1"},
])
def test_receipt_constraints_reject_bad_values_and_duplicate_identity(seeded, changes):
    before = snapshot(seeded)
    values = _receipt(receipt_reference="NEW", request_key="NEW")
    values.update(changes)
    with pytest.raises(IntegrityError):
        with seeded[1].begin() as session:
            session.execute(insert(seeded[2].tables["purchase_receipts"]).values(**values))
    assert snapshot(seeded) == before


@pytest.mark.parametrize("changes", [
    {"business_id": 1234}, {"business_id": 99}, {"purchase_receipt_id": 999},
    {"purchase_receipt_id": 2}, {"purchase_order_item_id": 999}, {"purchase_order_item_id": 2},
    {"business_id": None}, {"purchase_receipt_id": None}, {"purchase_order_item_id": None},
    {"received_qty": 0}, {"received_qty": -1}, {"received_qty": None},
    {},  # duplicate (purchase_receipt_id, purchase_order_item_id)
])
def test_line_constraints_reject_invalid_values_and_duplicates(seeded, changes):
    before = snapshot(seeded)
    with pytest.raises(IntegrityError):
        with seeded[1].begin() as session:
            values = _item(purchase_receipt_id=4) if changes else _item()
            values.update(changes)
            session.execute(insert(purchase_receipt_items).values(**values))
    assert snapshot(seeded) == before


def test_same_po_line_can_appear_on_distinct_receipts_and_keys_are_business_scoped(actor, seeded):
    with seeded[1].begin() as session:
        session.execute(insert(purchase_receipt_items).values(**_item(id=5, purchase_receipt_id=4)))
    repository = SQLAlchemyPurchaseReceivingRepository(seeded[1])
    assert repository.list_purchase_receipt_items(actor=actor, receipt_id=4)[0].purchase_order_item_id == 1
    rows = snapshot(seeded)["purchase_receipts"]
    assert rows[0].receipt_reference == rows[1].receipt_reference
    assert rows[0].request_key == rows[1].request_key


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("other_order_item", [3, 5])
def test_schema_gap_is_real_and_all_reads_fail_closed_on_mixed_po_lines(actor, seeded, operation, other_order_item):
    # Both composite FKs are satisfied: schema accepts a line from another PO
    # in the same business (same destination or different destination).
    with seeded[1].begin() as session:
        session.execute(insert(purchase_receipt_items).values(**_item(
            id=5, purchase_order_item_id=other_order_item)))
    before = snapshot(seeded)
    with pytest.raises(RepositoryPersistenceError, match="^Purchase receiving records could not be loaded.$"):
        # Even requesting the GOOD item cannot return part of an invalid receipt.
        lookup(SQLAlchemyPurchaseReceivingRepository(seeded[1]), operation, actor)
    assert snapshot(seeded) == before


def _corrupt(database, table, values):
    # Deliberately broken fixture data in a disposable SQLite file only.
    with database[0].connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(update(table).where(table.c.id == 1).values(**values))
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("linked_item", [999, 2])
def test_unresolved_or_foreign_business_po_item_fails_closed(actor, seeded, operation, linked_item):
    _corrupt(seeded, purchase_receipt_items, {"purchase_order_item_id": linked_item})
    with pytest.raises(RepositoryPersistenceError, match="^Purchase receiving records could not be loaded.$"):
        lookup(SQLAlchemyPurchaseReceivingRepository(seeded[1]), operation, actor)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("linked_order", [999, 2])
def test_missing_or_foreign_business_parent_cannot_expose_header_or_lines(actor, seeded, operation, linked_order):
    _corrupt(seeded, purchase_receipts, {"purchase_order_id": linked_order})
    with pytest.raises(RepositoryNotFoundError, match="^Purchase receipt not found.$"):
        lookup(SQLAlchemyPurchaseReceivingRepository(seeded[1]), operation, actor)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("failure", ["factory", "select", "second-select", "commit"])
def test_repository_failures_are_generic_rollback_and_close_owned_sessions(actor, seeded, operation, failure):
    before = snapshot(seeded)
    sessions, rolled_back = [], []
    class FailingSession(Session):
        def close(self):
            super().close()
            self.info["closed"] = True

        def execute(self, statement, *args, **kwargs):
            self.info["calls"] = self.info.get("calls", 0) + 1
            if failure == "select" or (failure == "second-select" and self.info["calls"] == 2):
                self.connection()
                raise OperationalError("private SQL and credentials", {}, Exception("secret"))
            return super().execute(statement, *args, **kwargs)

    def factory():
        if failure == "factory":
            raise OperationalError("private SQL", {}, Exception("secret"))
        session = FailingSession(bind=seeded[0])
        sessions.append(session)
        event.listen(session, "after_rollback", lambda _session: rolled_back.append(True))
        return session

    def fail_commit(_connection):
        raise OperationalError("private SQL and credentials", {}, Exception("secret"))
    if failure == "commit":
        event.listen(seeded[0], "commit", fail_commit)
    try:
        with pytest.raises(RepositoryPersistenceError) as caught:
            lookup(SQLAlchemyPurchaseReceivingRepository(factory), operation, actor)
        assert str(caught.value) == "Purchase receiving records could not be loaded."
        assert caught.value.__suppress_context__
    finally:
        if failure == "commit":
            event.remove(seeded[0], "commit", fail_commit)
    assert all(session.info.get("closed") and not session.in_transaction() for session in sessions)
    if failure != "factory":
        assert rolled_back == [True]
    assert snapshot(seeded) == before


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("state", ["active", "pending", "dirty", "deleted"])
def test_rejects_caller_unit_of_work_without_flushing_committing_or_closing(actor, seeded, operation, state):
    probe = seeded[2].tables["inventory_probe"]
    mapper_registry = registry()
    class Probe:
        pass
    mapper_registry.map_imperatively(Probe, probe)
    try:
        with seeded[1]() as borrowed:
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
            before_state = (set(borrowed.new), set(borrowed.dirty), set(borrowed.deleted))
            with pytest.raises(RepositoryPersistenceError, match="fresh database session"):
                lookup(SQLAlchemyPurchaseReceivingRepository(lambda: borrowed), operation, actor)
            assert borrowed.in_transaction()
            assert (set(borrowed.new), set(borrowed.dirty), set(borrowed.deleted)) == before_state
            borrowed.rollback()
    finally:
        mapper_registry.dispose()
    assert snapshot(seeded)["inventory_probe"] == [("unchanged",)]


def test_select_only_reads_preserve_every_table(actor, seeded):
    before, statements = snapshot(seeded), []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(seeded[0], "before_cursor_execute", observe)
    try:
        repository = SQLAlchemyPurchaseReceivingRepository(seeded[1])
        for operation in OPERATIONS:
            lookup(repository, operation, actor)
    finally:
        event.remove(seeded[0], "before_cursor_execute", observe)
    assert statements and all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)
    assert snapshot(seeded) == before


def test_preparation_remains_nonpersistent_and_port_exposes_only_reads(actor, seeded):
    before = snapshot(seeded)
    result = prepare_purchase_receiving_input(
        {"purchase_order_id": 999, "items": [
            {"purchase_order_line_id": 999, "received_quantity": 0}]},
        actor=actor, branch_scope=ResourceScope(10, 2), business_branch_count=1,
    )
    assert not result.errors  # Existing preliminary behavior; no eligibility claim.
    assert set(vars(result)) == {"errors"}
    expected = {"get_purchase_receipt", "get_purchase_receipt_item", "list_purchase_receipt_items"}
    for target in (PurchaseReceivingRepository, SQLAlchemyPurchaseReceivingRepository):
        assert {name for name in vars(target) if not name.startswith("_")} == expected
    assert snapshot(seeded) == before


@pytest.mark.parametrize("status", ["PENDING", "PARTIALLY_RECEIVED", "RECEIVED"])
def test_reads_do_not_invent_receiving_lifecycle_rules(actor, seeded, status):
    with seeded[1].begin() as session:
        session.execute(update(purchase_orders).where(purchase_orders.c.id == 1).values(status=status))
    repository = SQLAlchemyPurchaseReceivingRepository(seeded[1])
    assert repository.get_purchase_receipt(actor=actor, receipt_id=1).receipt_id == 1
    assert repository.list_purchase_receipt_items(actor=actor, receipt_id=1)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_factory_must_return_sqlalchemy_session(actor, operation):
    with pytest.raises(TypeError, match="SQLAlchemy Session"):
        lookup(SQLAlchemyPurchaseReceivingRepository(lambda: object()), operation, actor)


def test_factory_contract_and_no_production_installation():
    with pytest.raises(TypeError, match="callable"):
        SQLAlchemyPurchaseReceivingRepository(None)
    app = create_app({"TESTING": True, "APP_ENV": "test",
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert len(list(app.url_map.iter_rules())) == 49
    assert not any("purchase" in rule.rule or "receiv" in rule.endpoint for rule in app.url_map.iter_rules())
    assert SESSION_AUTH_EXTENSION not in app.extensions
    assert not any(isinstance(value, SQLAlchemyPurchaseReceivingRepository) for value in app.extensions.values())
    assert app.test_client().post("/api/purchase-receipts", json={}).status_code == 404
