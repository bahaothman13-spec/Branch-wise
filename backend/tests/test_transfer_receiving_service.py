"""SQLite-backed checks for secure, non-persistent transfer receipt preparation."""

from dataclasses import FrozenInstanceError, replace
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
from sqlalchemy.orm import sessionmaker

from app import create_app
from permissions import (
    AuthenticatedPrincipal,
    DASHBOARD_VIEW,
    ROLE_PERMISSIONS,
    TRANSFERS_CONFIRM_RECEIPT,
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
from branchwise.transfers.transfer_receiving_service import (
    TransferReceivingBlocker,
    TransferReceivingInputError,
    TransferReceivingPreparationState,
    prepare_transfer_receiving,
)


_REQUESTED_AT = datetime(2030, 1, 2, 3, 4, 5)
_DISCREPANCY_AT = datetime(2030, 1, 3, 3, 4, 5)
_FULL_RECEIPT = [
    {"product_variant_id": 3, "quantity": 3},
    {"product_variant_id": 4, "quantity": 2},
]


def _metadata():
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
    copied_transfers = stock_transfers.to_metadata(metadata)
    history_check = next(
        constraint
        for constraint in copied_transfers.constraints
        if constraint.name == "ck_stock_transfers_history"
    )
    history_check.sqltext = text(
        "event_history_json IS NULL OR "
        "MYSQL_JSON_TYPE(event_history_json)='ARRAY'"
    )
    copied_transfers.c.requested_at.server_default = DefaultClause(
        text("CURRENT_TIMESTAMP")
    )
    copied_items = stock_transfer_items.to_metadata(metadata)
    for name in ("discrepancy_reported_at", "discrepancy_resolved_at"):
        copied_items.c[name].server_default = None
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
    engine = create_engine("sqlite:///" + (tmp_path / "transfer_receiving.sqlite").as_posix())

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
        for name, rows in (
            ("branches", [(2, 10), (7, 10), (92, 99), (93, 99)]),
            ("users", [(1, 10), (2, 10), (91, 99)]),
            ("product_variants", [(3, 10), (4, 10), (5, 10), (93, 99)]),
        ):
            session.execute(
                insert(metadata.tables[name]),
                [
                    {"id": row_id, "business_id": business_id}
                    for row_id, business_id in rows
                ],
            )
        session.execute(
            insert(stock_transfers),
            [
                transfer_values(id=1, status="IN_TRANSIT"),
                transfer_values(
                    id=2,
                    business_id=99,
                    transfer_reference="FOREIGN",
                    request_key="foreign-receipt",
                    source_branch_id=92,
                    destination_branch_id=93,
                    requested_by_user_id=91,
                    status="IN_TRANSIT",
                ),
                transfer_values(
                    id=3,
                    transfer_reference="PENDING",
                    request_key="pending-receipt",
                    status="PENDING",
                ),
                transfer_values(
                    id=4,
                    transfer_reference="COMPLETED",
                    request_key="completed-receipt",
                    status="COMPLETED",
                ),
                transfer_values(
                    id=5,
                    transfer_reference="REJECTED",
                    request_key="rejected-receipt",
                    status="REJECTED",
                ),
                transfer_values(
                    id=6,
                    transfer_reference="CANCELLED",
                    request_key="cancelled-receipt",
                    status="CANCELLED",
                ),
                transfer_values(
                    id=7,
                    transfer_reference="OTHER-BRANCH",
                    request_key="other-branch-receipt",
                    source_branch_id=7,
                    destination_branch_id=2,
                    status="IN_TRANSIT",
                ),
            ],
        )
        session.execute(
            insert(stock_transfer_items),
            [
                item_values(id=11),
                item_values(
                    id=12,
                    variant_id=4,
                    requested_qty=2,
                    dispatched_qty=2,
                    received_qty=0,
                    in_transit_qty=2,
                ),
                item_values(
                    id=21,
                    business_id=99,
                    transfer_id=2,
                    variant_id=93,
                    requested_qty=1,
                    dispatched_qty=1,
                    received_qty=0,
                    in_transit_qty=1,
                ),
                item_values(
                    id=31,
                    transfer_id=3,
                    requested_qty=5,
                    dispatched_qty=0,
                    received_qty=0,
                    in_transit_qty=0,
                ),
                item_values(
                    id=41,
                    transfer_id=4,
                    requested_qty=5,
                    dispatched_qty=5,
                    received_qty=5,
                    in_transit_qty=0,
                ),
                item_values(
                    id=51,
                    transfer_id=5,
                    requested_qty=5,
                    dispatched_qty=0,
                    received_qty=0,
                    in_transit_qty=0,
                ),
                item_values(
                    id=61,
                    transfer_id=6,
                    requested_qty=5,
                    dispatched_qty=0,
                    received_qty=0,
                    in_transit_qty=0,
                ),
                item_values(
                    id=71,
                    transfer_id=7,
                    variant_id=5,
                    requested_qty=1,
                    dispatched_qty=1,
                    received_qty=0,
                    in_transit_qty=1,
                ),
            ],
        )
        session.execute(
            insert(metadata.tables["inventory_probe"]).values(value="unchanged")
        )
    try:
        yield engine, factory, metadata
    finally:
        engine.dispose()


def transfer_values(**changes):
    return dict(
        {
            "business_id": 10,
            "transfer_reference": "BR-36-1",
            "request_key": "receipt-1",
            "source_branch_id": 2,
            "destination_branch_id": 7,
            "requested_by_user_id": 1,
            "status": "IN_TRANSIT",
            "requested_at": _REQUESTED_AT,
            "event_history_json": None,
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
            "dispatched_qty": 5,
            "received_qty": 2,
            "returned_to_source_qty": 0,
            "written_off_qty": 0,
            "in_transit_qty": 3,
            "missing_qty": 0,
            "resolved_qty": 0,
        },
        **changes,
    )


def trusted_actor(
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


def receiving_input(transfer_id=1, items=None, **extra):
    return dict(
        {"transfer_id": transfer_id, "items": list(_FULL_RECEIPT if items is None else items)},
        **extra,
    )


def repository_for(database, session_factory=None):
    return SQLAlchemyStockTransferRepository(session_factory or database[1])


def test_owner_receives_a_typed_full_quantity_preparation(database):
    calls = []
    actual = repository_for(database)

    class RecordingRepository:
        def get_stock_transfer(self, *, actor, transfer_id, purpose):
            calls.append(("transfer", purpose))
            return actual.get_stock_transfer(
                actor=actor, transfer_id=transfer_id, purpose=purpose
            )

        def list_stock_transfer_items(self, *, actor, transfer_id, purpose):
            calls.append(("items", purpose))
            return actual.list_stock_transfer_items(
                actor=actor, transfer_id=transfer_id, purpose=purpose
            )

    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(),
        repository=RecordingRepository(),
    )

    assert calls == [
        ("transfer", TransferReadPurpose.CONFIRM_RECEIPT),
        ("items", TransferReadPurpose.CONFIRM_RECEIPT),
    ]
    assert result.state is TransferReceivingPreparationState.PREPARED_FOR_REVIEW
    assert result.expected_current_status == "IN_TRANSIT"
    assert result.blocker is None
    assert result.persisted is False
    assert result.stock_reserved is False
    assert result.disposition_required_before_inventory_effect is True
    assert [
        (
            line.transfer_item_id,
            line.product_variant_id,
            line.previously_received_qty,
            line.receipt_qty,
            line.in_transit_qty_before,
            line.in_transit_qty_after,
        )
        for line in result.lines
    ] == [(11, 3, 2, 3, 3, 0), (12, 4, 0, 2, 2, 0)]


def test_destination_branch_manager_can_prepare_receiving(database):
    manager = trusted_actor(Role.BRANCH_MANAGER, branch_id=7, user_id=2)
    result = prepare_transfer_receiving(
        actor=manager,
        receiving_input=receiving_input(),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.PREPARED_FOR_REVIEW


def test_source_only_manager_cannot_prepare_destination_receiving(database):
    manager = trusted_actor(Role.BRANCH_MANAGER, branch_id=2)
    with pytest.raises(RepositoryNotFoundError):
        prepare_transfer_receiving(
            actor=manager,
            receiving_input=receiving_input(),
            repository=repository_for(database),
        )


@pytest.mark.parametrize(
    "actor",
    [
        trusted_actor(
            Role.EMPLOYEE,
            branch_id=7,
            permissions={TRANSFERS_CONFIRM_RECEIPT},
        ),
        trusted_actor(permissions={DASHBOARD_VIEW}),
        object(),
    ],
)
def test_employee_missing_grant_and_invalid_actor_are_denied_before_reads(
    database, actor
):
    calls = []

    def session_factory():
        calls.append(True)
        return database[1]()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_receiving(
            actor=actor,
            receiving_input=receiving_input(),
            repository=repository_for(database, session_factory),
        )
    assert calls == []


def test_forged_principal_is_denied_before_reads(database):
    original = trusted_actor()
    forged_principal = AuthenticatedPrincipal(
        "Owner", 10, None, frozenset({TRANSFERS_CONFIRM_RECEIPT})
    )
    calls = []

    def session_factory():
        calls.append(True)
        return database[1]()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_receiving(
            actor=replace(original, principal=forged_principal),
            receiving_input=receiving_input(),
            repository=repository_for(database, session_factory),
        )
    assert calls == []


def test_request_scope_fields_and_disposition_are_not_trusted_or_accepted(database):
    calls = []

    def session_factory():
        calls.append(True)
        return database[1]()

    repo = repository_for(database, session_factory)
    for payload in (
        receiving_input(business_id=99),
        receiving_input(items=[{"product_variant_id": 3, "quantity": 3,
                                "disposition": "AVAILABLE"}]),
    ):
        with pytest.raises(TransferReceivingInputError):
            prepare_transfer_receiving(
                actor=trusted_actor(),
                receiving_input=payload,
                repository=repo,
            )
    assert calls == []


@pytest.mark.parametrize("transfer_id", [2, 999])
def test_foreign_business_and_missing_transfers_are_not_disclosed(
    database, transfer_id
):
    with pytest.raises(RepositoryNotFoundError):
        prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=receiving_input(transfer_id),
            repository=repository_for(database),
        )


def test_pending_transfer_is_blocked_without_loading_items(database):
    actual = repository_for(database)
    calls = []

    class RecordingRepository:
        def get_stock_transfer(self, *, actor, transfer_id, purpose):
            calls.append("transfer")
            return actual.get_stock_transfer(
                actor=actor, transfer_id=transfer_id, purpose=purpose
            )

        def list_stock_transfer_items(self, **kwargs):
            calls.append("items")
            return actual.list_stock_transfer_items(**kwargs)

    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(3),
        repository=RecordingRepository(),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.NOT_DISPATCHED
    assert result.expected_current_status == "PENDING"
    assert result.lines == ()
    assert calls == ["transfer"]


@pytest.mark.parametrize("transfer_id", [4, 5, 6])
def test_completed_rejected_and_cancelled_transfers_are_blocked(database, transfer_id):
    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(transfer_id),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.STATUS_NOT_RECEIVABLE
    assert result.lines == ()
    assert result.persisted is False


def test_unknown_or_cross_transfer_variant_is_blocked(database):
    for variant_id in (5, 93, 999):
        result = prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=receiving_input(
                items=[{"product_variant_id": variant_id, "quantity": 1}]
            ),
            repository=repository_for(database),
        )
        assert result.state is TransferReceivingPreparationState.BLOCKED
        assert result.blocker is TransferReceivingBlocker.UNKNOWN_TRANSFER_VARIANT
        assert result.lines == ()


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "1"])
def test_invalid_receipt_quantities_fail_validation_before_reads(database, quantity):
    calls = []

    def session_factory():
        calls.append(True)
        return database[1]()

    payload = receiving_input(
        items=[{"product_variant_id": 3, "quantity": quantity}]
    )
    with pytest.raises(TransferReceivingInputError):
        prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=payload,
            repository=repository_for(database, session_factory),
        )
    assert calls == []


def test_quantity_above_in_transit_is_blocked(database):
    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(
            items=[{"product_variant_id": 3, "quantity": 4}]
        ),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.QUANTITY_EXCEEDS_IN_TRANSIT


def test_duplicate_variant_references_are_blocked(database):
    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(
            items=[
                {"product_variant_id": 3, "quantity": 1},
                {"product_variant_id": 3, "quantity": 2},
            ]
        ),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.DUPLICATE_VARIANT
    assert result.lines == ()


def test_partial_receiving_is_blocked_pending_policy_confirmation(database):
    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(
            items=[{"product_variant_id": 3, "quantity": 2}]
        ),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.PARTIAL_RECEIPT_POLICY_UNRESOLVED
    assert result.lines == ()


def test_unresolved_persisted_discrepancy_blocks_receiving(database):
    with database[1].begin() as session:
        session.execute(
            update(stock_transfer_items)
            .where(stock_transfer_items.c.id == 11)
            .values(
                missing_qty=1,
                discrepancy_reported_by=1,
                discrepancy_reported_at=_DISCREPANCY_AT,
            )
        )

    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.DISCREPANCY_POLICY_UNRESOLVED


def test_returned_or_written_off_quantity_blocks_unresolved_resolution(database):
    with database[1].begin() as session:
        session.execute(
            update(stock_transfer_items)
            .where(stock_transfer_items.c.id == 11)
            .values(returned_to_source_qty=1, in_transit_qty=2)
        )

    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(
            items=[
                {"product_variant_id": 3, "quantity": 2},
                {"product_variant_id": 4, "quantity": 2},
            ]
        ),
        repository=repository_for(database),
    )

    assert result.state is TransferReceivingPreparationState.BLOCKED
    assert result.blocker is TransferReceivingBlocker.QUANTITY_RESOLUTION_POLICY_UNRESOLVED


def test_inconsistent_persisted_item_parent_fails_closed(database):
    actual = repository_for(database)

    class InconsistentRepository:
        def get_stock_transfer(self, **kwargs):
            return actual.get_stock_transfer(**kwargs)

        def list_stock_transfer_items(self, **kwargs):
            items = actual.list_stock_transfer_items(**kwargs)
            return (replace(items[0], transfer_id=7), *items[1:])

    with pytest.raises(RepositoryPersistenceError):
        prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=receiving_input(),
            repository=InconsistentRepository(),
        )


def test_inconsistent_persisted_quantity_conservation_fails_closed(database):
    with database[0].begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            update(stock_transfer_items)
            .where(stock_transfer_items.c.id == 11)
            .values(in_transit_qty=2)
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(RepositoryPersistenceError):
        prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=receiving_input(),
            repository=repository_for(database),
        )


def test_repository_failure_propagates_without_mutation(database):
    actual = repository_for(database)
    failure = RepositoryPersistenceError("Stock transfer records could not be loaded.")

    class FailingRepository:
        def get_stock_transfer(self, **kwargs):
            return actual.get_stock_transfer(**kwargs)

        def list_stock_transfer_items(self, **_kwargs):
            raise failure

    with pytest.raises(RepositoryPersistenceError) as raised:
        prepare_transfer_receiving(
            actor=trusted_actor(),
            receiving_input=receiving_input(),
            repository=FailingRepository(),
        )
    assert raised.value is failure
    with database[1]() as session:
        assert session.execute(
            select(stock_transfers.c.status).where(stock_transfers.c.id == 1)
        ).scalar_one() == "IN_TRANSIT"
        assert session.execute(
            select(stock_transfer_items.c.received_qty).where(
                stock_transfer_items.c.id == 11
            )
        ).scalar_one() == 2
        assert session.execute(
            select(database[2].tables["inventory_probe"].c.value)
        ).scalar_one() == "unchanged"


def test_preparation_is_immutable_and_makes_no_database_or_inventory_writes(database):
    statements = []
    event.listen(
        database[0],
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _params, _ctx, _many: statements.append(
            statement.lstrip().split(None, 1)[0].upper()
        ),
    )

    result = prepare_transfer_receiving(
        actor=trusted_actor(),
        receiving_input=receiving_input(),
        repository=repository_for(database),
    )
    with pytest.raises(FrozenInstanceError):
        result.state = TransferReceivingPreparationState.BLOCKED
    with pytest.raises(FrozenInstanceError):
        result.lines[0].receipt_qty = 0
    assert isinstance(result.lines, tuple)
    assert statements and set(statements) == {"SELECT"}


def test_receiving_service_does_not_register_a_production_route():
    app = create_app(
        {
            "TESTING": True,
            "APP_ENV": "test",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    routes = list(app.url_map.iter_rules())
    assert len(routes) == 49
    assert not any("transfer" in rule.rule.casefold() for rule in routes)
