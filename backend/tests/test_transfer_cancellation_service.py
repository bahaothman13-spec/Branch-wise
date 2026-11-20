from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import permissions
import pytest

from app import create_app
from branchwise.transfers.stock_transfer_contracts import StockTransferRecord
from branchwise.transfers.transfer_cancellation_service import (
    TransferCancellationBlocker,
    TransferCancellationEligibilityState,
    TransferCancellationInputError,
    evaluate_transfer_cancellation_eligibility,
    prepare_transfer_cancellation,
)
from permissions import (
    AuthenticatedPrincipal,
    Role,
    TRANSFERS_APPROVE_SOURCE,
    TRANSFERS_DISPATCH,
    TRANSFERS_REQUEST,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession
from transfer_rules import TransferStatus, validate_status_transition


def transfer_record(status="PENDING", *, transfer_id=7, source_branch_id=2,
                    destination_branch_id=9):
    return StockTransferRecord(
        id=transfer_id,
        business_id=10,
        transfer_reference="TR-7",
        request_key="request-7",
        source_branch_id=source_branch_id,
        destination_branch_id=destination_branch_id,
        requested_by_user_id=3,
        status=status,
        approved_by_user_id=None,
        dispatched_by_user_id=None,
        received_by_user_id=None,
        requested_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        dispatched_at=None,
        received_at=None,
        closed_at=None,
        notes=None,
        event_history_json=None,
    )


def actor(role=Role.OWNER, *, branch_id=None, grants=None):
    if grants is None:
        grants = {
            Role.OWNER: {
                TRANSFERS_REQUEST,
                TRANSFERS_APPROVE_SOURCE,
                TRANSFERS_DISPATCH,
            },
            Role.BRANCH_MANAGER: {
                TRANSFERS_REQUEST,
                TRANSFERS_APPROVE_SOURCE,
                TRANSFERS_DISPATCH,
            },
            Role.EMPLOYEE: {TRANSFERS_REQUEST},
        }[role]
    return AuthenticatedSession(
        user_id=1,
        access_version_at_login=0,
        principal=AuthenticatedPrincipal(
            role, 10, branch_id, frozenset(grants)
        ),
        expires_at=datetime(2031, 1, 1, tzinfo=timezone.utc),
        _session_token_digest="a" * 64,
        _csrf_token_digest="b" * 64,
    )


class ReadSpy:
    def __init__(self):
        self.calls = []

    def get_stock_transfer(self, **kwargs):
        self.calls.append(("get_stock_transfer", kwargs))
        raise AssertionError("Protected transfer reads must not occur.")

    def get_stock_transfer_item(self, **kwargs):
        self.calls.append(("get_stock_transfer_item", kwargs))
        raise AssertionError("Protected transfer reads must not occur.")

    def list_stock_transfer_items(self, **kwargs):
        self.calls.append(("list_stock_transfer_items", kwargs))
        raise AssertionError("Protected transfer reads must not occur.")


def evaluate(status, *, transfer_id=7, input_id=None):
    return evaluate_transfer_cancellation_eligibility(
        cancellation_input={"transfer_id": transfer_id if input_id is None else input_id},
        transfer=transfer_record(status, transfer_id=transfer_id),
    )


def test_valid_pending_request_is_only_lifecycle_eligible():
    result = evaluate("PENDING")

    assert result.state is TransferCancellationEligibilityState.LIFECYCLE_TRANSITION_ALLOWED
    assert result.lifecycle_eligible is True
    assert result.current_status == "PENDING"
    assert result.target_status == "CANCELLED"
    assert result.blocker is None
    assert result.cancellation_authorized is False
    assert result.persisted is False
    assert result.authoritative is False


@pytest.mark.parametrize(
    ("cancellation_input", "expected_errors"),
    [
        (None, {"body": "Transfer cancellation input must be a dictionary."}),
        ({}, {"transfer_id": "A positive integer transfer ID is required."}),
        (
            {"transfer_id": True},
            {"transfer_id": "A positive integer transfer ID is required."},
        ),
        (
            {"transfer_id": 7, "business_id": 10},
            {"body": "Unknown transfer cancellation fields are not allowed."},
        ),
    ],
)
def test_invalid_cancellation_input_uses_existing_validator(
    cancellation_input, expected_errors
):
    with pytest.raises(TransferCancellationInputError) as raised:
        evaluate_transfer_cancellation_eligibility(
            cancellation_input=cancellation_input,
            transfer=transfer_record(),
        )

    assert dict(raised.value.field_errors) == expected_errors


@pytest.mark.parametrize(
    ("status", "eligible", "blocker"),
    [
        ("PENDING", True, None),
        (
            "IN_TRANSIT",
            False,
            TransferCancellationBlocker.POST_DISPATCH_POLICY_UNRESOLVED,
        ),
        ("COMPLETED", False, TransferCancellationBlocker.TERMINAL_STATUS),
        ("REJECTED", False, TransferCancellationBlocker.TERMINAL_STATUS),
        ("CANCELLED", False, TransferCancellationBlocker.ALREADY_CANCELLED),
    ],
)
def test_only_developer1_permitted_cancellation_transition_is_eligible(
    status, eligible, blocker
):
    result = evaluate(status)

    assert result.lifecycle_eligible is eligible
    assert result.blocker is blocker
    assert result.target_status == ("CANCELLED" if eligible else None)
    if eligible:
        validate_status_transition(
            TransferStatus[status], TransferStatus.CANCELLED
        )
    else:
        with pytest.raises(ValueError):
            validate_status_transition(
                TransferStatus[status], TransferStatus.CANCELLED
            )


@pytest.mark.parametrize("status", ["UNKNOWN", "pending", "", None, 1])
def test_unknown_and_malformed_persisted_statuses_fail_closed(status):
    result = evaluate(status)

    assert result.state is TransferCancellationEligibilityState.BLOCKED
    assert result.lifecycle_eligible is False
    assert result.current_status is None
    assert result.target_status is None
    assert result.blocker is TransferCancellationBlocker.UNKNOWN_OR_MALFORMED_STATUS
    assert result.cancellation_authorized is False
    assert result.persisted is False
    assert result.authoritative is False


def test_transfer_id_must_match_validated_input():
    with pytest.raises(RepositoryPersistenceError):
        evaluate("PENDING", transfer_id=7, input_id=8)


def test_repeated_cancelled_state_is_blocked_without_persistence():
    first = evaluate("CANCELLED")
    repeated = evaluate("CANCELLED")

    assert first.blocker is TransferCancellationBlocker.ALREADY_CANCELLED
    assert repeated.blocker is TransferCancellationBlocker.ALREADY_CANCELLED
    assert first.persisted is False
    assert repeated.persisted is False


@pytest.mark.parametrize(
    "trusted_actor",
    [
        actor(Role.OWNER, grants={TRANSFERS_REQUEST}),
        actor(Role.OWNER, grants={TRANSFERS_APPROVE_SOURCE}),
        actor(Role.BRANCH_MANAGER, branch_id=2, grants={TRANSFERS_DISPATCH}),
        actor(Role.BRANCH_MANAGER, branch_id=9, grants={TRANSFERS_REQUEST}),
        actor(Role.EMPLOYEE, branch_id=9, grants={TRANSFERS_APPROVE_SOURCE}),
        object(),
    ],
)
def test_unapproved_or_forged_authority_is_denied_before_repository_reads(
    trusted_actor,
):
    repository = ReadSpy()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_cancellation(
            actor=trusted_actor,
            cancellation_input={"transfer_id": 7},
            repository=repository,
        )

    assert repository.calls == []


def test_forged_role_and_grants_cannot_enable_cancellation():
    forged_principal = AuthenticatedPrincipal(
        "Owner", 10, None, frozenset({TRANSFERS_APPROVE_SOURCE})
    )
    forged_actor = SimpleNamespace(
        user_id=1,
        access_version_at_login=0,
        principal=forged_principal,
    )
    repository = ReadSpy()

    with pytest.raises(RepositoryAuthorizationError):
        prepare_transfer_cancellation(
            actor=forged_actor,
            cancellation_input={"transfer_id": 7},
            repository=repository,
        )

    assert repository.calls == []


def test_branch_data_is_not_cancellation_authority():
    source_manager = actor(Role.BRANCH_MANAGER, branch_id=2)
    destination_manager = actor(Role.BRANCH_MANAGER, branch_id=9)
    record = transfer_record(
        "PENDING", source_branch_id=source_manager.principal.branch_id,
        destination_branch_id=destination_manager.principal.branch_id,
    )
    result = evaluate_transfer_cancellation_eligibility(
        cancellation_input={"transfer_id": record.id},
        transfer=record,
    )
    repository = ReadSpy()

    assert result.lifecycle_eligible is True
    assert result.cancellation_authorized is False
    for branch_actor in (source_manager, destination_manager):
        with pytest.raises(RepositoryAuthorizationError):
            prepare_transfer_cancellation(
                actor=branch_actor,
                cancellation_input={"transfer_id": record.id},
                repository=repository,
            )
    assert repository.calls == []


def test_no_cancellation_permission_or_repository_purpose_is_added():
    from branchwise.transfers.stock_transfer_contracts import TransferReadPurpose

    assert not hasattr(permissions, "TRANSFERS_CANCEL")
    assert not hasattr(TransferReadPurpose, "CANCEL")


def test_eligibility_preserves_input_and_transfer_and_returns_immutable_result():
    record = transfer_record()
    request = {"transfer_id": record.id}
    original_request = request.copy()
    result = evaluate_transfer_cancellation_eligibility(
        cancellation_input=request,
        transfer=record,
    )

    assert request == original_request
    assert record == transfer_record()
    with pytest.raises(FrozenInstanceError):
        result.lifecycle_eligible = False
    assert result.authoritative is False
    assert result.persisted is False


def test_eligibility_has_no_repository_or_inventory_write_capability():
    record = transfer_record()
    result = evaluate_transfer_cancellation_eligibility(
        cancellation_input={"transfer_id": record.id},
        transfer=record,
    )

    assert "repository" not in inspect.signature(
        evaluate_transfer_cancellation_eligibility
    ).parameters
    assert result.persisted is False
    assert result.cancellation_authorized is False
    assert record.status == "PENDING"


def test_cancellation_foundation_does_not_register_a_production_route():
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
