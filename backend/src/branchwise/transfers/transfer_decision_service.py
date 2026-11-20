"""Internal, non-persistent preparation for BE-35 transfer decisions."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal

from branchwise.transfers.stock_transfer_contracts import (
    StockTransferRepository,
    TransferReadPurpose,
    TransferStatusRecord,
)
from branchwise.transfers.transfer_decision_validation import (
    validate_transfer_decision_input,
)
from isolation_rules import can_access_transfer, require_resource_access
from permissions import (
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    TRANSFERS_APPROVE_SOURCE,
    is_trusted_principal,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryNotFoundError,
    RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from transfer_rules import TransferStatus, validate_status_transition


_PREPARATION_ERROR = "Transfer decision could not be prepared."


class TransferDecisionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class TransferDecisionPreparationState(str, Enum):
    PREPARED_NOT_PERSISTED = "prepared_not_persisted"
    BLOCKED = "blocked"


class TransferDecisionBlocker(str, Enum):
    APPROVAL_TRANSITION_UNRESOLVED = "approval_transition_unresolved"
    CURRENT_STATUS_NOT_PENDING = "current_status_not_pending"
    LIFECYCLE_TRANSITION_NOT_ALLOWED = "lifecycle_transition_not_allowed"


@dataclass(frozen=True)
class TransferDecisionPreparation:
    """Authorized decision facts only; this plan is neither persisted nor reserved."""

    transfer_id: int
    action: TransferDecisionAction
    state: TransferDecisionPreparationState
    expected_current_status: TransferStatusRecord
    target_status: TransferStatusRecord | None
    blocker: TransferDecisionBlocker | None
    persisted: Literal[False] = False


class TransferDecisionInputError(ValueError):
    """Raised when the existing BE-35 envelope validator rejects the input."""

    def __init__(self, field_errors):
        self.field_errors = MappingProxyType(dict(field_errors))
        super().__init__("Transfer decision input is invalid.")


def _authorize_actor(actor: AuthenticatedSession) -> AuthenticatedPrincipal:
    if (
        type(actor) is not AuthenticatedSession
        or not is_positive_integer(actor.user_id)
        or not is_trusted_principal(actor.principal)
        or actor.principal.role not in (Role.OWNER, Role.BRANCH_MANAGER)
    ):
        raise RepositoryAuthorizationError("Permission denied.")

    principal = actor.principal
    try:
        require_resource_access(
            principal,
            TRANSFERS_APPROVE_SOURCE,
            ResourceScope(principal.business_id, principal.branch_id),
        )
    except PermissionError:
        raise RepositoryAuthorizationError("Permission denied.") from None
    return principal


def _current_status(persisted_status: TransferStatusRecord) -> TransferStatus:
    try:
        return TransferStatus[persisted_status]
    except (KeyError, TypeError):
        raise RepositoryPersistenceError(_PREPARATION_ERROR) from None


def _preparation(
    transfer_id: int,
    action: TransferDecisionAction,
    current_status: TransferStatus,
    *,
    target_status: TransferStatus | None = None,
    blocker: TransferDecisionBlocker | None = None,
) -> TransferDecisionPreparation:
    if blocker is not None:
        return TransferDecisionPreparation(
            transfer_id=transfer_id,
            action=action,
            state=TransferDecisionPreparationState.BLOCKED,
            expected_current_status=current_status.name,
            target_status=None,
            blocker=blocker,
        )
    return TransferDecisionPreparation(
        transfer_id=transfer_id,
        action=action,
        state=TransferDecisionPreparationState.PREPARED_NOT_PERSISTED,
        expected_current_status=current_status.name,
        target_status=target_status.name if target_status is not None else None,
        blocker=None,
    )


def prepare_transfer_decision(
    *,
    actor: AuthenticatedSession,
    decision_input: object,
    repository: StockTransferRepository,
) -> TransferDecisionPreparation:
    """Prepare an authorized decision against the persisted transfer state.

    Rejection is prepared only when the existing lifecycle rules allow
    PENDING -> REJECTED. Approval is always blocked for a PENDING transfer
    until the team defines how it maps to the persisted lifecycle; no APPROVED
    status exists. This operation never persists, locks, or reserves a result.
    """
    principal = _authorize_actor(actor)

    errors = validate_transfer_decision_input(decision_input)
    if errors:
        raise TransferDecisionInputError(errors)
    if not isinstance(decision_input, dict):
        raise TransferDecisionInputError(
            {"body": "Transfer decision must be a dictionary."}
        )

    if not callable(getattr(repository, "get_stock_transfer", None)):
        raise TypeError("repository must provide get_stock_transfer.")

    transfer_id = decision_input["transfer_id"]
    action = TransferDecisionAction(decision_input["action"])
    transfer = repository.get_stock_transfer(
        actor=actor,
        transfer_id=transfer_id,
        purpose=TransferReadPurpose.APPROVE_SOURCE,
    )

    if type(transfer.id) is not int or transfer.id != transfer_id:
        raise RepositoryNotFoundError("Stock transfer not found.")
    if transfer.business_id != principal.business_id:
        raise RepositoryNotFoundError("Stock transfer not found.")
    if not can_access_transfer(
        principal,
        TRANSFERS_APPROVE_SOURCE,
        ResourceScope(transfer.business_id, transfer.source_branch_id),
        ResourceScope(transfer.business_id, transfer.destination_branch_id),
        permission_branch="source",
    ):
        raise RepositoryNotFoundError("Stock transfer not found.")

    current_status = _current_status(transfer.status)
    if action is TransferDecisionAction.APPROVE:
        if current_status is not TransferStatus.PENDING:
            return _preparation(
                transfer_id,
                action,
                current_status,
                blocker=TransferDecisionBlocker.CURRENT_STATUS_NOT_PENDING,
            )
        return _preparation(
            transfer_id,
            action,
            current_status,
            blocker=TransferDecisionBlocker.APPROVAL_TRANSITION_UNRESOLVED,
        )

    try:
        validate_status_transition(current_status, TransferStatus.REJECTED)
    except ValueError:
        return _preparation(
            transfer_id,
            action,
            current_status,
            blocker=TransferDecisionBlocker.LIFECYCLE_TRANSITION_NOT_ALLOWED,
        )
    return _preparation(
        transfer_id,
        action,
        current_status,
        target_status=TransferStatus.REJECTED,
    )
