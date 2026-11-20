"""Disposable SQLite checks for internal stock-transfer read persistence."""

from dataclasses import FrozenInstanceError
from datetime import datetime
import json

import pytest
from sqlalchemy import (
    Column,
    DefaultClause,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from db import db
from permissions import (
    AuthenticatedPrincipal,
    ROLE_PERMISSIONS,
    TRANSFERS_APPROVE_SOURCE,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_DISPATCH,
    TRANSFERS_REQUEST,
    Role,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession
from branchwise.transfers.stock_transfer_contracts import TransferReadPurpose
from branchwise.transfers.stock_transfer_model import (
    stock_transfer_items,
    stock_transfers,
)
from branchwise.transfers.stock_transfer_repository import (
    SQLAlchemyStockTransferRepository,
)


_REQUESTED_AT = datetime(2030, 1, 2, 3, 4, 5)


def _metadata():
    """Create isolated parent stubs and copy production mappings for tests."""
    metadata = MetaData()
    Table("businesses", metadata, Column("id", Integer, primary_key=True))
    for name in ("branches", "users", "product_variants"):
        Table(
            name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("business_id", Integer, nullable=False),
            UniqueConstraint("business_id", "id"),
        )
    copied = (
        stock_transfers.to_metadata(metadata),
        stock_transfer_items.to_metadata(metadata),
    )
    for mapped in copied:
        for constraint in mapped.constraints:
            if constraint.name == "ck_stock_transfers_history":
                constraint.sqltext = text(
                    "event_history_json IS NULL OR "
                    "MYSQL_JSON_TYPE(event_history_json)='ARRAY'"
                )
        for name in (
            "requested_at",
            "dispatched_at",
            "received_at",
            "closed_at",
            "discrepancy_reported_at",
            "discrepancy_resolved_at",
        ):
            if name in mapped.c and mapped.c[name].server_default is not None:
                mapped.c[name].server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    Table("inventory_probe", metadata, Column("value", Text, nullable=False))
    return metadata


def _sqlite_json_type(raw):
    value = json.loads(raw)
    if isinstance(value, list):
        return "ARRAY"
    if isinstance(value, dict):
        return "OBJECT"
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "STRING"


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "stock_transfers.sqlite").as_posix())

    @event.listens_for(engine, "connect")
    def setup(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function(
            "MYSQL_JSON_TYPE", 1, _sqlite_json_type, deterministic=True
        )

    metadata = _metadata()
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        session.execute(insert(metadata.tables["businesses"]), [{"id": 10}, {"id": 99}])
        for name, values in (
            ("branches", [(2, 10), (7, 10), (92, 99), (93, 99)]),
            ("users", [(1, 10), (2, 10), (91, 99)]),
            ("product_variants", [(3, 10), (4, 10), (93, 99)]),
        ):
            session.execute(
                insert(metadata.tables[name]),
                [{"id": key, "business_id": business} for key, business in values],
            )
        session.execute(
            insert(stock_transfers),
            [
                transfer_values(id=1),
                transfer_values(
                    id=2,
                    business_id=99,
                    transfer_reference="FOREIGN",
                    request_key="foreign-request",
                    source_branch_id=92,
                    destination_branch_id=93,
                    requested_by_user_id=91,
                ),
                transfer_values(
                    id=3,
                    source_branch_id=7,
                    destination_branch_id=2,
                    transfer_reference="OTHER-BRANCH",
                    request_key="other-branch-request",
                    status="IN_TRANSIT",
                    event_history_json=None,
                ),
            ],
        )
        session.execute(
            insert(stock_transfer_items),
            [
                item_values(id=1),
                item_values(
                    id=2,
                    business_id=99,
                    transfer_id=2,
                    variant_id=93,
                    requested_qty=2,
                ),
                item_values(
                    id=3,
                    transfer_id=3,
                    variant_id=4,
                    requested_qty=2,
                    dispatched_qty=2,
                    received_qty=2,
                ),
            ],
        )
        session.execute(insert(metadata.tables["inventory_probe"]).values(value="unchanged"))
    try:
        yield engine, factory, metadata
    finally:
        engine.dispose()


def transfer_values(**changes):
    return dict(
        {
            "business_id": 10,
            "transfer_reference": "BR-34-1",
            "request_key": "request-1",
            "source_branch_id": 2,
            "destination_branch_id": 7,
            "requested_by_user_id": 1,
            "status": "PENDING",
            "requested_at": _REQUESTED_AT,
            "notes": None,
            "event_history_json": [{"event": "requested", "meta": {"actor": "owner"}}],
        },
        **changes,
    )


def item_values(**changes):
    return dict(
        {
            "business_id": 10,
            "transfer_id": 1,
            "variant_id": 3,
            "requested_qty": 5,
            "dispatched_qty": 0,
            "received_qty": 0,
            "returned_to_source_qty": 0,
            "written_off_qty": 0,
            "in_transit_qty": 0,
            "missing_qty": 0,
            "resolved_qty": 0,
        },
        **changes,
    )


def actor(
    role=Role.OWNER,
    *,
    business_id=10,
    branch_id=None,
    permissions=None,
    user_id=1,
):
    if permissions is None:
        permissions = ROLE_PERMISSIONS[role]
    principal = AuthenticatedPrincipal(
        role, business_id, branch_id, frozenset(permissions)
    )
    return AuthenticatedSession(
        user_id,
        0,
        principal,
        datetime(2030, 1, 1),
        "a" * 64,
        "b" * 64,
    )


def repository(database, factory=None):
    return SQLAlchemyStockTransferRepository(factory or database[1])


def test_mapping_matches_draft_columns_constraints_and_mysql_ddl():
    expected = (
        (
            stock_transfers,
            {
                "id", "business_id", "transfer_reference", "request_key",
                "source_branch_id", "destination_branch_id", "requested_by_user_id",
                "status", "approved_by_user_id", "dispatched_by_user_id",
                "received_by_user_id", "requested_at", "dispatched_at", "received_at",
                "closed_at", "notes", "event_history_json",
            },
            {
                "uq_stock_transfers_bid_id", "uq_stock_transfers_reference",
                "uq_stock_transfers_request", "ck_stock_transfers_branches",
                "ck_stock_transfers_history", "fk_stock_transfers_business_id",
                "fk_stock_transfers_source_branch_id",
                "fk_stock_transfers_destination_branch_id",
                "fk_stock_transfers_requested_by_user_id",
                "fk_stock_transfers_approved_by_user_id",
                "fk_stock_transfers_dispatched_by_user_id",
                "fk_stock_transfers_received_by_user_id",
            },
            {"ix_stock_transfers_1", "ix_stock_transfers_2"},
        ),
        (
            stock_transfer_items,
            {
                "id", "business_id", "transfer_id", "variant_id", "requested_qty",
                "dispatched_qty", "received_qty", "returned_to_source_qty",
                "written_off_qty", "in_transit_qty", "missing_qty", "resolved_qty",
                "discrepancy_reported_by", "discrepancy_reported_at",
                "discrepancy_resolved_at", "discrepancy_notes",
            },
            {
                "uq_stock_transfer_items_bid_id", "uq_stock_transfer_items_variant",
                "ck_stock_transfer_items_qty", "ck_stock_transfer_items_conservation",
                "ck_stock_transfer_items_discrepancy",
                "ck_stock_transfer_items_discrepancy_report",
                "ck_stock_transfer_items_discrepancy_resolution",
                "fk_stock_transfer_items_business_id",
                "fk_stock_transfer_items_transfer_id",
                "fk_stock_transfer_items_variant_id",
                "fk_stock_transfer_items_discrepancy_reported_by",
            },
            set(),
        ),
    )
    metadata = _metadata()
    assert stock_transfers.metadata is db.metadata
    assert stock_transfer_items.metadata is db.metadata
    for mapped, columns, constraints, indexes in expected:
        assert set(mapped.c.keys()) == columns
        assert {constraint.name for constraint in mapped.constraints if constraint.name} == constraints
        assert {index.name for index in mapped.indexes} == indexes
        assert tuple(mapped.primary_key.columns.keys()) == ("id",)
        assert not any(column.nullable for column in mapped.c if column.name in {
            "id", "business_id", "transfer_id", "variant_id", "requested_qty",
            "dispatched_qty", "received_qty", "returned_to_source_qty",
            "written_off_qty", "in_transit_qty", "missing_qty", "resolved_qty",
            "transfer_reference", "request_key", "source_branch_id",
            "destination_branch_id", "requested_by_user_id", "status", "requested_at",
        })
        copied = metadata.tables[mapped.name]
        for column in mapped.c:
            if column.server_default is not None:
                copied.c[column.name].server_default = column.server_default
        ddl = str(CreateTable(copied).compile(dialect=mysql.dialect()))
        assert "ENGINE=InnoDB" in ddl
        assert "CHARSET=utf8mb4" in ddl
        assert "utf8mb4_0900_ai_ci" in ddl
        assert "UNSIGNED" in ddl
    transfer_ddl = str(
        CreateTable(metadata.tables["stock_transfers"]).compile(dialect=mysql.dialect())
    )
    item_ddl = str(
        CreateTable(metadata.tables["stock_transfer_items"]).compile(dialect=mysql.dialect())
    )
    for fragment in (
        "status ENUM('PENDING','IN_TRANSIT','COMPLETED','REJECTED','CANCELLED') "
        "NOT NULL DEFAULT 'PENDING'",
        "requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "UNIQUE (business_id, transfer_reference)",
        "UNIQUE (business_id, request_key)",
        "FOREIGN KEY(business_id, source_branch_id) REFERENCES branches "
        "(business_id, id)",
        "FOREIGN KEY(business_id, destination_branch_id) REFERENCES branches "
        "(business_id, id)",
    ):
        assert fragment in transfer_ddl
    for fragment in (
        "requested_qty INTEGER UNSIGNED NOT NULL",
        "UNIQUE (transfer_id, variant_id)",
        "FOREIGN KEY(business_id, transfer_id) REFERENCES stock_transfers "
        "(business_id, id)",
        "FOREIGN KEY(business_id, variant_id) REFERENCES product_variants "
        "(business_id, id)",
        "CHECK (dispatched_qty = received_qty + returned_to_source_qty "
        "+ written_off_qty + in_transit_qty)",
    ):
        assert fragment in item_ddl


def test_same_business_transfer_item_and_parent_records(database):
    repo = repository(database)
    owner = actor()

    transfer = repo.get_stock_transfer(
        actor=owner,
        transfer_id=1,
        purpose=TransferReadPurpose.APPROVE_SOURCE,
    )
    item = repo.get_stock_transfer_item(
        actor=owner,
        item_id=1,
        purpose=TransferReadPurpose.DISPATCH,
    )
    items = repo.list_stock_transfer_items(
        actor=owner,
        transfer_id=1,
        purpose=TransferReadPurpose.CONFIRM_RECEIPT,
    )

    assert (transfer.business_id, transfer.source_branch_id, transfer.destination_branch_id) == (
        10, 2, 7
    )
    assert transfer.status == "PENDING"
    assert transfer.requested_at == _REQUESTED_AT
    assert (item.transfer_id, item.variant_id, item.requested_qty) == (1, 3, 5)
    assert items == (item,)


def test_branch_manager_is_restricted_to_operation_branch(database):
    repo = repository(database)
    source_manager = actor(Role.BRANCH_MANAGER, branch_id=2)
    destination_manager = actor(Role.BRANCH_MANAGER, branch_id=7, user_id=2)

    assert repo.get_stock_transfer(
        actor=source_manager,
        transfer_id=1,
        purpose=TransferReadPurpose.DISPATCH,
    ).source_branch_id == 2
    with pytest.raises(RepositoryNotFoundError):
        repo.get_stock_transfer(
            actor=source_manager,
            transfer_id=1,
            purpose=TransferReadPurpose.CONFIRM_RECEIPT,
        )
    assert repo.get_stock_transfer(
        actor=destination_manager,
        transfer_id=1,
        purpose=TransferReadPurpose.CONFIRM_RECEIPT,
    ).destination_branch_id == 7
    with pytest.raises(RepositoryNotFoundError):
        repo.list_stock_transfer_items(
            actor=destination_manager,
            transfer_id=1,
            purpose=TransferReadPurpose.APPROVE_SOURCE,
        )


def test_employee_missing_forged_and_general_view_permissions_deny_before_db(database):
    calls = []

    def factory():
        calls.append(True)
        return database[1]()

    repo = repository(database, factory)
    employee_with_forged_grants = actor(
        Role.EMPLOYEE,
        branch_id=2,
        permissions={
            TRANSFERS_APPROVE_SOURCE,
            TRANSFERS_DISPATCH,
            TRANSFERS_CONFIRM_RECEIPT,
        },
    )
    owner_without_grant = actor(permissions={TRANSFERS_APPROVE_SOURCE})
    for invalid_actor, purpose in (
        (employee_with_forged_grants, TransferReadPurpose.DISPATCH),
        (owner_without_grant, TransferReadPurpose.DISPATCH),
        (actor(), TRANSFERS_REQUEST),
    ):
        with pytest.raises(RepositoryAuthorizationError):
            repo.get_stock_transfer(
                actor=invalid_actor,
                transfer_id=1,
                purpose=purpose,
            )
    assert calls == []


def test_cross_business_transfer_and_item_reads_are_indistinguishable(database):
    repo = repository(database)
    owner = actor()
    for lookup in (
        lambda: repo.get_stock_transfer(
            actor=owner, transfer_id=2, purpose=TransferReadPurpose.DISPATCH
        ),
        lambda: repo.get_stock_transfer_item(
            actor=owner, item_id=2, purpose=TransferReadPurpose.DISPATCH
        ),
    ):
        with pytest.raises(RepositoryNotFoundError):
            lookup()


def test_invalid_missing_identifiers_and_caller_owned_session(database):
    repo = repository(database)
    owner = actor()
    for invalid in (0, -1, True, "1", None):
        with pytest.raises(ValueError):
            repo.get_stock_transfer(
                actor=owner,
                transfer_id=invalid,
                purpose=TransferReadPurpose.DISPATCH,
            )
    with pytest.raises(RepositoryNotFoundError):
        repo.get_stock_transfer(
            actor=owner, transfer_id=999, purpose=TransferReadPurpose.DISPATCH
        )

    caller_session = database[1]()
    caller_session.begin()
    with pytest.raises(RepositoryPersistenceError):
        repository(database, lambda: caller_session).get_stock_transfer(
            actor=owner, transfer_id=1, purpose=TransferReadPurpose.DISPATCH
        )
    assert caller_session.in_transaction()
    caller_session.rollback()
    caller_session.close()


def test_transfer_item_parent_integrity_and_duplicate_variant_constraint(database):
    repo = repository(database)
    with pytest.raises(RepositoryNotFoundError):
        repo.get_stock_transfer_item(
            actor=actor(),
            item_id=2,
            purpose=TransferReadPurpose.DISPATCH,
        )

    with database[1].begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                insert(stock_transfer_items).values(
                    id=4,
                    business_id=10,
                    transfer_id=1,
                    variant_id=3,
                    requested_qty=1,
                )
            )
    with database[1].begin() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                insert(stock_transfer_items).values(
                    id=5,
                    business_id=10,
                    transfer_id=2,
                    variant_id=4,
                    requested_qty=1,
                )
            )


def test_inconsistent_persisted_item_fails_closed(database):
    with database[0].begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            update(stock_transfer_items)
            .where(stock_transfer_items.c.id == 1)
            .values(dispatched_qty=1)
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(RepositoryPersistenceError, match="could not be loaded"):
        repository(database).get_stock_transfer_item(
            actor=actor(),
            item_id=1,
            purpose=TransferReadPurpose.DISPATCH,
        )


def test_status_quantities_json_and_detached_records_are_preserved(database):
    repo = repository(database)
    transfer = repo.get_stock_transfer(
        actor=actor(),
        transfer_id=3,
        purpose=TransferReadPurpose.DISPATCH,
    )
    item = repo.get_stock_transfer_item(
        actor=actor(),
        item_id=3,
        purpose=TransferReadPurpose.DISPATCH,
    )
    pending = repo.get_stock_transfer(
        actor=actor(),
        transfer_id=1,
        purpose=TransferReadPurpose.DISPATCH,
    )

    assert transfer.status == "IN_TRANSIT"
    assert (item.requested_qty, item.dispatched_qty, item.received_qty) == (2, 2, 2)
    assert pending.event_history_json[0]["meta"]["actor"] == "owner"
    with pytest.raises(FrozenInstanceError):
        transfer.status = "COMPLETED"
    with pytest.raises(TypeError):
        pending.event_history_json[0]["meta"]["actor"] = "changed"


def test_repository_uses_independent_sessions(database):
    sessions = []

    def factory():
        session = database[1]()
        sessions.append(session)
        return session

    repo = repository(database, factory)
    repo.get_stock_transfer(
        actor=actor(), transfer_id=1, purpose=TransferReadPurpose.DISPATCH
    )
    repo.list_stock_transfer_items(
        actor=actor(), transfer_id=1, purpose=TransferReadPurpose.DISPATCH
    )
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert all(not session.in_transaction() for session in sessions)


def test_repository_reads_are_select_only_and_inventory_is_unchanged(database):
    statements = []
    event.listen(
        database[0],
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _params, _ctx, _many: statements.append(
            statement.lstrip().split(None, 1)[0].upper()
        ),
    )
    repo = repository(database)
    repo.get_stock_transfer(
        actor=actor(), transfer_id=1, purpose=TransferReadPurpose.DISPATCH
    )
    repo.get_stock_transfer_item(
        actor=actor(), item_id=1, purpose=TransferReadPurpose.DISPATCH
    )
    repo.list_stock_transfer_items(
        actor=actor(), transfer_id=1, purpose=TransferReadPurpose.DISPATCH
    )
    with database[1]() as session:
        assert session.execute(
            select(database[2].tables["inventory_probe"].c.value)
        ).scalar_one() == "unchanged"
    assert statements
    assert set(statements) == {"SELECT"}


def test_permission_constants_are_operation_specific():
    assert TransferReadPurpose.APPROVE_SOURCE.value == TRANSFERS_APPROVE_SOURCE
    assert TransferReadPurpose.DISPATCH.value == TRANSFERS_DISPATCH
    assert TransferReadPurpose.CONFIRM_RECEIPT.value == TRANSFERS_CONFIRM_RECEIPT
    assert TRANSFERS_REQUEST not in {purpose.value for purpose in TransferReadPurpose}
