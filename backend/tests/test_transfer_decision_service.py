"""SQLite-backed tests for secure, non-persistent BE-35 decision preparation."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime

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
)
from sqlalchemy.orm import sessionmaker

from app import create_app
from db import db
from permissions import (
    AuthenticatedPrincipal,
    DASHBOARD_VIEW,
    ROLE_PERMISSIONS,
    TRANSFERS_APPROVE_SOURCE,
    Role,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession
from branchwise.transfers.stock_transfer_contracts import (
    TransferReadPurpose,
)
from branchwise.transfers.stock_transfer_model import stock_transfers
from branchwise.transfers.stock_transfer_repository import (
    SQLAlchemyStockTransferRepository,
)
from branchwise.transfers.transfer_decision_service import (
    TransferDecisionAction,
    TransferDecisionBlocker,
    TransferDecisionInputError,
    TransferDecisionPreparationState,
    prepare_transfer_decision,
)


_REQUESTED_AT = datetime(2030, 1, 2, 3, 4, 5)


def _metadata():
    metadata = MetaData()
    Table("businesses", metadata, Column("id", Integer, primary_key=True))
    for name in ("branches", "users"):
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
    Table("inventory_probe", metadata, Column("value", Text, nullable=False))
    return metadata


def _sqlite_json_type(raw):
    import json

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
    engine = create_engine("sqlite:///" + (tmp_path / "transfer_decisions.sqlite").as_posix())

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
        ):
            session.execute(
                insert(metadata.tables[name]),
                [{"id": row_id, "business_id": business_id} for row_id, business_id in rows],
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
                    status="REJECTED",
                    transfer_reference="REJECTED",
                    request_key="rejected-request",
                ),
                transfer_values(
                    id=4,
                    status="CANCELLED",
                    transfer_reference="CANCELLED",
                    request_key="cancelled-request",
                ),
                transfer_values(
                    id=5,
                    status="COMPLETED",
                    transfer_reference="COMPLETED",
                    request_key="completed-request",
                ),
                transfer_values(
                    id=6,
                    status="IN_TRANSIT",
                    transfer_reference="IN-TRANSIT",
                    request_key="in-transit-request",
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
            "transfer_reference": "BR-35-1",
            "request_key": "decision-1",
            "source_branch_id": 2,
            "destination_branch_id": 7,
            "requested_by_user_id": 1,
            "status": "PENDING",
            "requested_at": _REQUESTED_AT,
            "event_history_json": None,
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


def decision(action="reject", transfer_id=1, **extra):
    return dict({"transfer_id": transfer_id, "action": action}, **extra)


def service_for(database, session_factory=None):
    return SQLAlchemyStockTransferRepository(session_factory or database[1])


def test_owner_can_prepare_rejection_but_does_not_persist(database):
    statements = []
    event.listen(
        database[0],
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _params, _ctx, _many: statements.append(
            statement.lstrip().split(None, 1)[0].upper()
        ),
    )

    result = prepare_transfer_decision(
        actor=trusted_actor(),
        decision_input=decision("reject"),
        repository=service_for(database),
    )

    assert result.action is TransferDecisionAction.REJECT
    assert result.state is TransferDecisionPreparationState.PREPARED_NOT_PERSISTED
    assert result.expected_current_status == "PENDING"
    assert result.target_status == "REJECTED"
    assert result.blocker is None
    assert result.persisted is False
    assert statements == ["SELECT"]
    with database[1]() as session:
        assert session.execute(
            select(stock_transfers.c.status).where(stock_transfers.c.id == 1)
        ).scalar_one() == "PENDING"
        assert session.execute(
            select(database[2].tables["inventory_probe"].c.value)
        ).scalar_one() == "unchanged"


def test_source_branch_manager_can_prepare_rejection(database):
    manager = trusted_actor(Role.BRANCH_MANAGER, branch_id=2)
    result = prepare_transfer_decision(
        actor=manager,
        decision_input=decision("reject"),
        repository=service_for(database),
    )

    assert result.state is TransferDecisionPreparationState.PREPARED_NOT_PERSISTED
    assert result.target_status == "REJECTED"


def test_approval_is_blocked_without_inventing_a_status(database):
    result = prepare_transfer_decision(
        actor=trusted_actor(),
        decision_input=decision("approve"),
        repository=service_for(database),
    )

    assert result.action is TransferDecisionAction.APPROVE
    assert result.state is TransferDecisionPreparationState.BLOCKED
    assert result.expected_current_status == "PENDING"
    assert result.target_status is None
    assert result.blocker is TransferDecisionBlocker.APPROVAL_TRANSITION_UNRESOLVED
    assert result.persisted is False
    assert "APPROVED" not in {result.expected_current_status, result.target_status}


@pytest.mark.parametrize("transfer_id", [3, 4, 5, 6])
@pytest.mark.parametrize("action", ["approve", "reject"])
def test_non_pending_transfers_are_blocked(database, transfer_id, action):
    result = prepare_transfer_decision(
        actor=trusted_actor(),
        decision_input=decision(action, transfer_id),
        repository=service_for(database),
    )

    assert result.state is TransferDecisionPreparationState.BLOCKED
    assert result.target_status is None
    assert result.persisted is False
    if action == "approve":
        assert result.blocker is TransferDecisionBlocker.CURRENT_STATUS_NOT_PENDING
    else:
        assert result.blocker is TransferDecisionBlocker.LIFECYCLE_TRANSITION_NOT_ALLOWED


def test_destination_only_manager_cannot_read_or_prepare_source_decision(database):
    manager = trusted_actor(Role.BRANCH_MANAGER, branch_id=7, user_id=2)

    with pytest.raises(RepositoryNotFoundError):
        prepare_transfer_decision(
            actor=manager,
            decision_input=decision("reject"),
            repository=service_for(database),
        )


@pytest.mark.parametrize(
    "actor",
    [
        trusted_actor(
            Role.EMPLOYEE,
            branch_id=2,
            permissions={TRANSFERS_APPROVE_SOURCE},
        ),
        trusted_actor(permissions={DASHBOARD_VIEW}),
        object(),
    ],
)
def test_unauthorized_or_untrusted_actor_is_denied_before_repository_access(
    database, actor
):
    calls = []

    def factory():
        calls.append(True)
        return database[1]()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_decision(
            actor=actor,
            decision_input=decision("reject"),
            repository=service_for(database, factory),
        )
    assert calls == []


def test_forged_principal_identity_is_denied_before_repository_access(database):
    original = trusted_actor()
    forged_principal = AuthenticatedPrincipal(
        "Owner", 10, None, frozenset({TRANSFERS_APPROVE_SOURCE})
    )
    forged_actor = replace(original, principal=forged_principal)
    calls = []

    def factory():
        calls.append(True)
        return database[1]()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_decision(
            actor=forged_actor,
            decision_input=decision("reject"),
            repository=service_for(database, factory),
        )
    assert calls == []


def test_forged_request_scope_and_invalid_action_are_rejected_before_read(database):
    calls = []

    def factory():
        calls.append(True)
        return database[1]()

    repo = service_for(database, factory)
    with pytest.raises(TransferDecisionInputError) as forged_scope:
        prepare_transfer_decision(
            actor=trusted_actor(),
            decision_input=decision("reject", business_id=99, role="Owner"),
            repository=repo,
        )
    assert "body" in forged_scope.value.field_errors
    with pytest.raises(TransferDecisionInputError) as bad_action:
        prepare_transfer_decision(
            actor=trusted_actor(),
            decision_input=decision("APPROVE"),
            repository=repo,
        )
    with pytest.raises(TransferDecisionInputError):
        prepare_transfer_decision(
            actor=trusted_actor(),
            decision_input=None,
            repository=repo,
        )
    assert "action" in bad_action.value.field_errors
    assert calls == []


@pytest.mark.parametrize("transfer_id", [2, 999])
def test_foreign_business_and_missing_transfers_are_not_disclosed(
    database, transfer_id
):
    with pytest.raises(RepositoryNotFoundError):
        prepare_transfer_decision(
            actor=trusted_actor(),
            decision_input=decision("reject", transfer_id),
            repository=service_for(database),
        )


def test_service_defensively_checks_business_and_source_scope():
    authorized = trusted_actor()
    base = _fake_transfer()

    class FakeRepository:
        def __init__(self, record):
            self.record = record

        def get_stock_transfer(self, *, actor, transfer_id, purpose):
            assert actor is authorized
            assert transfer_id == 1
            assert purpose is TransferReadPurpose.APPROVE_SOURCE
            return self.record

    for record in (
        replace(base, business_id=99),
        replace(base, source_branch_id=7),
        replace(base, id=2),
    ):
        with pytest.raises(RepositoryNotFoundError):
            prepare_transfer_decision(
                actor=authorized,
                decision_input=decision("reject"),
                repository=FakeRepository(record),
            )


def _fake_transfer():
    from branchwise.transfers.stock_transfer_contracts import StockTransferRecord

    return StockTransferRecord(
        id=1,
        business_id=10,
        transfer_reference="BR-35-1",
        request_key="decision-1",
        source_branch_id=2,
        destination_branch_id=7,
        requested_by_user_id=1,
        status="PENDING",
        approved_by_user_id=None,
        dispatched_by_user_id=None,
        received_by_user_id=None,
        requested_at=_REQUESTED_AT,
        dispatched_at=None,
        received_at=None,
        closed_at=None,
        notes=None,
        event_history_json=None,
    )


def test_repository_failure_is_propagated_without_becoming_a_plan():
    failure = RepositoryPersistenceError("Stock transfer records could not be loaded.")

    class FailingRepository:
        def get_stock_transfer(self, *, actor, transfer_id, purpose):
            raise failure

    with pytest.raises(RepositoryPersistenceError) as raised:
        prepare_transfer_decision(
            actor=trusted_actor(),
            decision_input=decision("reject"),
            repository=FailingRepository(),
        )
    assert raised.value is failure


def test_preparation_result_is_immutable_and_requires_a_recheck(database):
    result = prepare_transfer_decision(
        actor=trusted_actor(),
        decision_input=decision("reject"),
        repository=service_for(database),
    )
    with pytest.raises(FrozenInstanceError):
        result.target_status = "COMPLETED"


def test_service_does_not_register_a_production_route():
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
