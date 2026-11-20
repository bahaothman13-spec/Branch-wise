"""Disposable SQLite checks; MySQL DDL compilation is not live verification.

Only test metadata is created, with portable timestamp defaults. SQLite does
not emulate MySQL ENUM/unsigned/DECIMAL enforcement, collation or ON UPDATE.
"""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from unittest.mock import Mock

import pytest
from sqlalchemy import (
    Column, DefaultClause, ForeignKeyConstraint, Integer, MetaData, Table, Text,
    UniqueConstraint, create_engine, event, func, insert, select, text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app import create_app
from db import db
from permissions import (
    AuthenticatedPrincipal, PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED,
    ResourceScope, Role,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryNotFoundError, RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession, SESSION_AUTH_EXTENSION
from branchwise.purchasing.purchase_order_contracts import (
    PurchaseOrderItemRecord, PurchaseOrderRecord, PurchaseOrderRepository,
)
from branchwise.purchasing.purchase_order_model import purchase_order_items, purchase_orders
from branchwise.purchasing.purchase_order_repository import SQLAlchemyPurchaseOrderRepository
from branchwise.purchasing.purchase_order_service import prepare_purchase_order_input
from branchwise.suppliers.supplier_model import suppliers


@pytest.fixture
def actor():
    return AuthenticatedSession(
        1, 0, AuthenticatedPrincipal(Role.OWNER, 10, None, frozenset({PURCHASES_MANAGE})),
        datetime(2030, 1, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64,
    )


def _metadata():
    """Private parent stubs never enter shared production metadata."""
    metadata = MetaData()
    Table("businesses", metadata, Column("id", Integer, primary_key=True))
    for name in ("branches", "users", "product_variants"):
        Table(name, metadata, Column("id", Integer, primary_key=True),
              Column("business_id", Integer, nullable=False), UniqueConstraint("business_id", "id"))
    for source in (suppliers, purchase_orders, purchase_order_items):
        copied = source.to_metadata(metadata)
        for name in ("ordered_at", "created_at", "updated_at"):
            if name in copied.c:
                copied.c[name].server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    Table("inventory_probe", metadata, Column("value", Text, nullable=False))
    return metadata


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "purchase_orders.sqlite").as_posix())

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
        session.execute(insert(suppliers), [
            {"id": 1, "business_id": 10, "name": "Supplier"},
            {"id": 91, "business_id": 99, "name": "Foreign supplier"},
        ])
        session.execute(insert(metadata.tables["inventory_probe"]).values(value="unchanged"))
    try:
        yield engine, factory, metadata
    finally:
        engine.dispose()


def order_values(**changes):
    return dict({"business_id": 10, "branch_id": 2, "supplier_id": 1,
                 "purchase_reference": "APPROVED-TEST-1", "ordered_by_user_id": 1,
                 "expected_total_cost": Decimal("6.72")}, **changes)


def item_values(**changes):
    return dict({"business_id": 10, "purchase_order_id": 1, "variant_id": 3,
                 "ordered_qty": 3, "unit_cost": Decimal("0.10"),
                 "line_total": Decimal("0.30")}, **changes)


@pytest.fixture
def seeded(database):
    with database[1].begin() as session:
        session.execute(insert(purchase_orders), [
            order_values(id=1),
            order_values(id=2, business_id=99, branch_id=92, supplier_id=91,
                         ordered_by_user_id=91),
            order_values(id=3, branch_id=7, purchase_reference="OTHER-BRANCH"),
            order_values(id=4, purchase_reference="EMPTY"),
        ])
        session.execute(insert(purchase_order_items), [
            item_values(id=1),
            item_values(id=2, business_id=99, purchase_order_id=2, variant_id=93),
            item_values(id=3, purchase_order_id=3),
            item_values(id=4, variant_id=4, ordered_qty=2, unit_cost=Decimal("3.21"),
                        line_total=Decimal("6.42")),
        ])
    return database


def lookup(repository, operation, actor, *, order_id=1, item_id=1):
    if operation == "order":
        return repository.get_purchase_order(actor=actor, purchase_order_id=order_id)
    if operation == "item":
        return repository.get_purchase_order_item(
            actor=actor, purchase_order_id=order_id, item_id=item_id,
        )
    return repository.list_purchase_order_items(actor=actor, purchase_order_id=order_id)


def snapshot(database):
    with database[1]() as session:
        return {name: session.execute(select(table)).all()
                for name, table in database[2].tables.items()}


def test_mapping_columns_types_defaults_and_mysql_ddl():
    metadata = _metadata()
    for table, columns, constraints, indexes in (
        (purchase_orders,
         {"id", "business_id", "branch_id", "supplier_id", "purchase_reference", "status",
          "ordered_by_user_id", "ordered_at", "expected_total_cost", "created_at", "updated_at"},
         {"uq_purchase_orders_bid_id", "uq_purchase_orders_reference", "ck_purchase_orders_cost",
          "fk_purchase_orders_business_id", "fk_purchase_orders_branch_id",
          "fk_purchase_orders_supplier_id", "fk_purchase_orders_ordered_by_user_id"},
         {"ix_purchase_orders_1", "ix_purchase_orders_2"}),
        (purchase_order_items,
         {"id", "business_id", "purchase_order_id", "variant_id", "ordered_qty", "unit_cost", "line_total"},
         {"uq_purchase_order_items_bid_id", "uq_purchase_order_items_variant",
          "ck_purchase_order_items_qty", "ck_purchase_order_items_total",
          "fk_purchase_order_items_business_id", "fk_purchase_order_items_purchase_order_id",
          "fk_purchase_order_items_variant_id"},
         {"ix_purchase_order_items_1"}),
    ):
        assert table.metadata is db.metadata
        assert set(table.c.keys()) == columns
        assert not any(column.nullable for column in table.c)
        assert {c.name for c in table.constraints if c.name} == constraints
        assert {index.name for index in table.indexes} == indexes
        assert tuple(table.primary_key.columns.keys()) == ("id",)
        for column in table.c:
            if column.name == "id" or column.name.endswith("_id"):
                assert str(column.type.compile(dialect=mysql.dialect())) == "BIGINT UNSIGNED"
        # Restore exact production defaults for compilation only.
        copied = metadata.tables[table.name]
        for name in ("ordered_at", "created_at", "updated_at"):
            if name in table.c:
                copied.c[name].server_default = table.c[name].server_default
        ddl = str(CreateTable(copied).compile(dialect=mysql.dialect()))
        for fragment in ("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT", "ENGINE=InnoDB",
                         "CHARSET=utf8mb4", "utf8mb4_0900_ai_ci"):
            assert fragment in ddl
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                assert constraint.ondelete is None and constraint.onupdate is None

    orders_ddl = str(CreateTable(metadata.tables["purchase_orders"]).compile(dialect=mysql.dialect()))
    items_ddl = str(CreateTable(metadata.tables["purchase_order_items"]).compile(dialect=mysql.dialect()))
    for fragment in (
        "purchase_reference VARCHAR(40) NOT NULL",
        "status ENUM('PENDING','PARTIALLY_RECEIVED','RECEIVED') NOT NULL DEFAULT 'PENDING'",
        "expected_total_cost DECIMAL(14, 2) NOT NULL",
        "ordered_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)",
        "UNIQUE (business_id, id)", "UNIQUE (business_id, purchase_reference)",
        "CHECK (expected_total_cost >= 0)",
        "FOREIGN KEY(business_id) REFERENCES businesses (id)",
        "FOREIGN KEY(business_id, branch_id) REFERENCES branches (business_id, id)",
        "FOREIGN KEY(business_id, supplier_id) REFERENCES suppliers (business_id, id)",
        "FOREIGN KEY(business_id, ordered_by_user_id) REFERENCES users (business_id, id)",
    ):
        assert fragment in orders_ddl
    for fragment in (
        "ordered_qty INTEGER UNSIGNED NOT NULL", "unit_cost DECIMAL(14, 2) NOT NULL",
        "line_total DECIMAL(14, 2) NOT NULL", "UNIQUE (business_id, id)",
        "UNIQUE (purchase_order_id, variant_id)", "CHECK (ordered_qty > 0)",
        "CHECK (unit_cost >= 0 AND line_total = ROUND(ordered_qty * unit_cost, 2))",
        "FOREIGN KEY(business_id) REFERENCES businesses (id)",
        "FOREIGN KEY(business_id, purchase_order_id) REFERENCES purchase_orders (business_id, id)",
        "FOREIGN KEY(business_id, variant_id) REFERENCES product_variants (business_id, id)",
    ):
        assert fragment in items_ddl
    assert str(CreateIndex(next(i for i in purchase_orders.indexes if i.name.endswith("_1"))).compile(
        dialect=mysql.dialect())) == (
            "CREATE INDEX ix_purchase_orders_1 ON purchase_orders (business_id, branch_id, ordered_at, status)"
        )
    for column in (purchase_orders.c.expected_total_cost, purchase_order_items.c.unit_cost,
                   purchase_order_items.c.line_total):
        assert (column.type.precision, column.type.scale, column.type.asdecimal) == (14, 2, True)
    assert purchase_orders.c.updated_at.server_onupdate is not None
    assert purchase_orders.c.purchase_reference.type.length == 40
    assert purchase_order_items.c.unit_cost.default is None
    assert purchase_orders.c.expected_total_cost.default is None
    assert {index.name: tuple(index.columns.keys()) for index in purchase_orders.indexes} == {
        "ix_purchase_orders_1": ("business_id", "branch_id", "ordered_at", "status"),
        "ix_purchase_orders_2": ("business_id", "supplier_id", "ordered_at"),
    }
    assert {index.name: tuple(index.columns.keys()) for index in purchase_order_items.indexes} == {
        "ix_purchase_order_items_1": ("business_id", "variant_id"),
    }


def test_detached_records_across_independent_sessions(actor, seeded):
    sessions = []
    def factory():
        session = seeded[1]()
        sessions.append(session)
        return session
    repository: PurchaseOrderRepository = SQLAlchemyPurchaseOrderRepository(factory)
    order = repository.get_purchase_order(actor=actor, purchase_order_id=1)
    item = repository.get_purchase_order_item(actor=actor, purchase_order_id=1, item_id=1)
    items = repository.list_purchase_order_items(actor=actor, purchase_order_id=1)
    assert type(order) is PurchaseOrderRecord and type(item) is PurchaseOrderItemRecord
    assert order.business_id == item.business_id == 10
    assert order.status == "PENDING" and order.purchase_reference == "APPROVED-TEST-1"
    assert order.ordered_at is not None and order.created_at is not None and order.updated_at is not None
    assert order.expected_total_cost == Decimal("6.72")
    assert item.unit_cost == Decimal("0.10") and item.line_total == Decimal("0.30")
    assert type(items) is tuple and [line.item_id for line in items] == [1, 4]
    assert items[0] == item
    assert len({id(session) for session in sessions}) == 3
    assert all(not session.in_transaction() for session in sessions)
    assert not hasattr(order, "_sa_instance_state") and not hasattr(item, "_sa_instance_state")
    with pytest.raises(FrozenInstanceError):
        item.unit_cost = Decimal("9.99")
    with pytest.raises(FrozenInstanceError):
        order.business_id = 99
    with seeded[1]() as independent:
        assert independent.scalar(select(purchase_orders.c.expected_total_cost).where(
            purchase_orders.c.id == order.purchase_order_id)) == order.expected_total_cost


@pytest.mark.parametrize("amount", ["0.00", "0.01", "0.10", "123456789012.34", "999999999999.99"])
def test_exact_decimal_at_approved_precision(actor, database, amount):
    value = Decimal(amount)
    with database[1].begin() as session:
        session.execute(insert(purchase_orders).values(**order_values(id=1, expected_total_cost=value)))
        session.execute(insert(purchase_order_items).values(**item_values(
            id=1, ordered_qty=1, unit_cost=value, line_total=value)))
    repository = SQLAlchemyPurchaseOrderRepository(database[1])
    with localcontext() as context:
        context.prec = 4
        context.clear_flags()
        before = context.copy()
        order = repository.get_purchase_order(actor=actor, purchase_order_id=1)
        item = repository.get_purchase_order_item(actor=actor, purchase_order_id=1, item_id=1)
        assert context.flags == before.flags
    for actual in (order.expected_total_cost, item.unit_cost, item.line_total):
        assert type(actual) is Decimal and actual == value and actual.as_tuple().exponent == -2


@pytest.mark.parametrize("operation", ["order", "item", "list"])
@pytest.mark.parametrize("order_id,item_id", [(2, 2), (999, 999)])
def test_cross_business_and_missing_resources_are_indistinguishable(actor, seeded, operation, order_id, item_id):
    repository = SQLAlchemyPurchaseOrderRepository(seeded[1])
    with pytest.raises(RepositoryNotFoundError) as caught:
        lookup(repository, operation, actor, order_id=order_id, item_id=item_id)
    expected = "Purchase order item not found." if operation == "item" else "Purchase order not found."
    assert str(caught.value) == expected


def test_lines_are_scoped_to_parent_and_empty_order_is_distinguished(actor, seeded):
    repository = SQLAlchemyPurchaseOrderRepository(seeded[1])
    for order_id, item_id in ((1, 2), (1, 3), (3, 1), (4, 1), (1, 999)):
        with pytest.raises(RepositoryNotFoundError):
            repository.get_purchase_order_item(actor=actor, purchase_order_id=order_id, item_id=item_id)
    assert repository.list_purchase_order_items(actor=actor, purchase_order_id=4) == ()
    foreign = replace(actor, principal=replace(actor.principal, business_id=99))
    assert repository.get_purchase_order(actor=foreign, purchase_order_id=2).business_id == 99
    assert repository.list_purchase_order_items(actor=foreign, purchase_order_id=2)[0].item_id == 2


@pytest.mark.parametrize("operation", ["order", "item", "list"])
def test_manager_is_limited_to_assigned_branch(actor, seeded, operation):
    manager = replace(actor, principal=AuthenticatedPrincipal(
        Role.BRANCH_MANAGER, 10, 2, frozenset({PURCHASES_MANAGE_AUTHORIZED})))
    repository = SQLAlchemyPurchaseOrderRepository(seeded[1])
    assert lookup(repository, operation, manager)
    with pytest.raises(RepositoryNotFoundError):
        lookup(repository, operation, manager, order_id=3, item_id=3)
    assert lookup(repository, operation, actor, order_id=3, item_id=3)


@pytest.mark.parametrize("operation", ["order", "item", "list"])
@pytest.mark.parametrize("kind", ["none", "mapping", "bad-user", "bad-business", "bad-role",
                                 "owner-no-grant", "owner-wrong-grant", "manager-no-grant",
                                 "manager-wrong-grant", "manager-no-branch", "employee"])
def test_unauthorized_call_never_opens_session(actor, operation, kind):
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
    with pytest.raises(RepositoryAuthorizationError, match="Permission denied"):
        lookup(SQLAlchemyPurchaseOrderRepository(factory), operation, denied)
    factory.assert_not_called()


@pytest.mark.parametrize("operation", ["order", "item", "list"])
@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, "1", 1.0, Decimal("1"), [], {}])
def test_invalid_order_ids_fail_before_session(actor, operation, invalid):
    factory = Mock()
    with pytest.raises(ValueError, match="purchase_order_id"):
        lookup(SQLAlchemyPurchaseOrderRepository(factory), operation, actor, order_id=invalid)
    factory.assert_not_called()


@pytest.mark.parametrize("invalid", [None, True, 0, -1, "1", 1.0])
def test_invalid_item_ids_fail_before_session(actor, invalid):
    factory = Mock()
    with pytest.raises(ValueError, match="item_id"):
        lookup(SQLAlchemyPurchaseOrderRepository(factory), "item", actor, item_id=invalid)
    factory.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"business_id": 1234}, {"branch_id": 1234}, {"branch_id": 92},
    {"supplier_id": 1234}, {"supplier_id": 91},
    {"ordered_by_user_id": 1234}, {"ordered_by_user_id": 91},
    {"expected_total_cost": Decimal("-0.01")}, {"expected_total_cost": None},
    {"purchase_reference": None}, {"branch_id": None},
])
def test_order_constraints_reject_invalid_references_and_values(database, changes):
    with pytest.raises(IntegrityError):
        with database[1].begin() as session:
            session.execute(insert(purchase_orders).values(**order_values(**changes)))
    with database[1]() as session:
        assert session.scalar(select(func.count()).select_from(purchase_orders)) == 0


@pytest.mark.parametrize("changes", [
    {"business_id": 99, "variant_id": 93}, {"business_id": 1234},
    {"purchase_order_id": 2}, {"purchase_order_id": 999},
    {"variant_id": 93}, {"variant_id": 999}, {"variant_id": None},
    {"ordered_qty": 0}, {"ordered_qty": -1}, {"ordered_qty": None},
    {"unit_cost": Decimal("-0.10"), "line_total": Decimal("-0.30")},
    {"line_total": Decimal("0.31")}, {"line_total": None}, {"unit_cost": None},
])
def test_item_constraints_reject_invalid_references_and_values(seeded, changes):
    before = snapshot(seeded)
    with pytest.raises(IntegrityError):
        with seeded[1].begin() as session:
            session.execute(insert(purchase_order_items).values(**item_values(**changes)))
    assert snapshot(seeded) == before


def test_unique_reference_per_business_and_duplicate_variant_constraint(seeded):
    before = snapshot(seeded)
    for table, values in (
        (purchase_orders, order_values()),
        (purchase_order_items, item_values()),
    ):
        with pytest.raises(IntegrityError):
            with seeded[1].begin() as session:
                session.execute(insert(table).values(**values))
    assert snapshot(seeded) == before
    # The same reference in businesses 10 and 99 and the same variant in
    # different orders were already committed by the fixture.
    assert before["purchase_orders"][0].purchase_reference == before["purchase_orders"][1].purchase_reference


def test_test_transaction_rolls_back_header_and_lines_on_duplicate_variant(database):
    before = snapshot(database)
    with pytest.raises(IntegrityError):
        with database[1].begin() as session:
            session.execute(insert(purchase_orders).values(**order_values(id=1)))
            session.execute(insert(purchase_order_items).values(**item_values()))
            session.execute(insert(purchase_order_items).values(**item_values()))
    assert snapshot(database) == before


@pytest.mark.parametrize("operation,failure", [
    (operation, failure) for operation in ("order", "item", "list")
    for failure in ("factory", "select", "commit", "second-select")
    if failure != "second-select" or operation == "list"
])
def test_database_failures_are_safe_and_owned_transactions_rollback(actor, seeded, operation, failure):
    before = snapshot(seeded)
    sessions, rolled_back = [], []
    class FailingSession(Session):
        def close(self):
            super().close()
            self.info["closed"] = True

        def execute(self, statement, *args, **kwargs):
            self.info["calls"] = self.info.get("calls", 0) + 1
            if failure == "select" or (failure == "second-select" and self.info["calls"] == 2):
                # Force a real transaction so rollback behavior is observable.
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
            lookup(SQLAlchemyPurchaseOrderRepository(factory), operation, actor)
        expected = ("Purchase order could not be loaded." if operation == "order"
                    else "Purchase order items could not be loaded.")
        assert str(caught.value) == expected and caught.value.__suppress_context__
    finally:
        if failure == "commit":
            event.remove(seeded[0], "commit", fail_commit)
    assert all(not session.in_transaction() for session in sessions)
    assert all(session.info.get("closed") for session in sessions)
    if failure != "factory":
        assert rolled_back == [True]
    assert snapshot(seeded) == before


@pytest.mark.parametrize("operation", ["order", "item", "list"])
def test_rejects_caller_owned_transaction_without_touching_it(actor, seeded, operation):
    probe = seeded[2].tables["inventory_probe"]
    with seeded[1]() as borrowed:
        borrowed.execute(insert(probe).values(value="caller work"))
        with pytest.raises(RepositoryPersistenceError, match="fresh"):
            lookup(SQLAlchemyPurchaseOrderRepository(lambda: borrowed), operation, actor)
        assert borrowed.in_transaction()
        assert borrowed.scalar(select(func.count()).select_from(probe)) == 2
        borrowed.rollback()
    with seeded[1]() as independent:
        assert independent.scalar(select(func.count()).select_from(probe)) == 1


def test_read_operations_emit_only_select_and_preserve_all_tables(actor, seeded):
    before, statements = snapshot(seeded), []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(seeded[0], "before_cursor_execute", observe)
    try:
        repository = SQLAlchemyPurchaseOrderRepository(seeded[1])
        for operation in ("order", "item", "list"):
            lookup(repository, operation, actor)
    finally:
        event.remove(seeded[0], "before_cursor_execute", observe)
    assert statements and all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)
    assert snapshot(seeded) == before


def test_preparation_still_cannot_persist_and_port_exposes_no_writes(actor, seeded):
    before = snapshot(seeded)
    result = prepare_purchase_order_input(
        {"supplier_id": 1, "branch_id": 2, "items": [
            {"product_id": 999, "quantity": 1, "purchase_cost": "0.001"}]},
        actor=actor, branch_scope=ResourceScope(10, 2), business_branch_count=1,
    )
    assert not result.errors  # Preliminary validation still accepts unresolved product/cost.
    assert set(vars(result)) == {"errors"}
    expected = {"get_purchase_order", "get_purchase_order_item", "list_purchase_order_items"}
    for target in (PurchaseOrderRepository, SQLAlchemyPurchaseOrderRepository):
        assert {name for name in vars(target) if not name.startswith("_")} == expected
    assert snapshot(seeded) == before


@pytest.mark.parametrize("status", ["PENDING", "PARTIALLY_RECEIVED", "RECEIVED"])
def test_all_approved_stored_statuses_can_be_read(actor, database, status):
    with database[1].begin() as session:
        session.execute(insert(purchase_orders).values(**order_values(id=1, status=status)))
    result = SQLAlchemyPurchaseOrderRepository(database[1]).get_purchase_order(
        actor=actor, purchase_order_id=1,
    )
    assert result.status == status


@pytest.mark.parametrize("operation", ["order", "item", "list"])
def test_factory_must_return_session(actor, operation):
    with pytest.raises(TypeError, match="SQLAlchemy Session"):
        lookup(SQLAlchemyPurchaseOrderRepository(lambda: object()), operation, actor)


def test_factory_contract_and_no_production_installation():
    with pytest.raises(TypeError, match="callable"):
        SQLAlchemyPurchaseOrderRepository(None)
    app = create_app({"TESTING": True, "APP_ENV": "test",
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert not any("purchase" in rule.rule or "purchase" in rule.endpoint
                   for rule in app.url_map.iter_rules())
    assert SESSION_AUTH_EXTENSION not in app.extensions
    assert not any(isinstance(value, SQLAlchemyPurchaseOrderRepository) for value in app.extensions.values())
    assert app.test_client().post("/api/purchase-orders", json={}).status_code == 404
