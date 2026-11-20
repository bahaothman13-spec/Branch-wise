"""Internal, non-authoritative stock-transfer cancellation eligibility checks."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Literal, NoReturn

from branchwise.transfers.stock_transfer_contracts import (
    StockTransferRecord,
    StockTransferRepository,
    TransferStatusRecord,
)
from branchwise.transfers.transfer_cancellation_validation import (
    validate_transfer_cancellation_input,
)
from repository_contracts import (
    RepositoryAuthorizationError,
    RepositoryPersistenceError,
)
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from transfer_rules import TransferStatus, validate_status_transition


_ELIGIBILITY_ERROR = "Transfer cancellation eligibility could not be evaluated."


class TransferCancellationEligibilityState(str, Enum):
    LIFECYCLE_TRANSITION_ALLOWED = "lifecycle_transition_allowed"
    BLOCKED = "blocked"


class TransferCancellationBlocker(str, Enum):
    POST_DISPATCH_POLICY_UNRESOLVED = "post_dispatch_policy_unresolved"
    TERMINAL_STATUS = "terminal_status"
    ALREADY_CANCELLED = "already_cancelled"
    UNKNOWN_OR_MALFORMED_STATUS = "unknown_or_malformed_status"


@dataclass(frozen=True)
class TransferCancellationEligibility:
    """Lifecycle facts only; never authorization or confirmation to cancel."""

    transfer_id: int
    current_status: TransferStatusRecord | None
    state: TransferCancellationEligibilityState
    lifecycle_eligible: bool
    target_status: Literal["CANCELLED"] | None
    blocker: TransferCancellationBlocker | None
    cancellation_authorized: Literal[False] = False
    persisted: Literal[False] = False
    authoritative: Literal[False] = False


class TransferCancellationInputError(ValueError):
    """Raised when the existing BE-37 envelope validator rejects the input."""

    def __init__(self, field_errors):
        self.field_errors = MappingProxyType(dict(field_errors))
        super().__init__("Transfer cancellation input is invalid.")


def _result(
    transfer_id: int,
    *,
    current_status: TransferStatusRecord | None,
    blocker: TransferCancellationBlocker | None = None,
) -> TransferCancellationEligibility:
    eligible = blocker is None
    return TransferCancellationEligibility(
        transfer_id=transfer_id,
        current_status=current_status,
        state=(
            TransferCancellationEligibilityState.LIFECYCLE_TRANSITION_ALLOWED
            if eligible
            else TransferCancellationEligibilityState.BLOCKED
        ),
        lifecycle_eligible=eligible,
        target_status="CANCELLED" if eligible else None,
        blocker=blocker,
    )


def evaluate_transfer_cancellation_eligibility(
    *,
    cancellation_input: object,
    transfer: StockTransferRecord,
) -> TransferCancellationEligibility:
    """Evaluate status-transition rules for trusted, already-loaded data only.

    This pure helper does not authenticate, authorize, or load a transfer.
    Possession of ``transfer`` is not cancellation authority; callers must not
    use this result to expose data or perform cancellation without an approved
    authorization contract and transactional revalidation.
    """
    errors = validate_transfer_cancellation_input(cancellation_input)
    if errors:
        raise TransferCancellationInputError(errors)
    if not isinstance(cancellation_input, dict):
        raise TransferCancellationInputError(
            {"body": "Transfer cancellation input must be a dictionary."}
        )
    if (
        type(transfer) is not StockTransferRecord
        or not is_positive_integer(transfer.id)
        or transfer.id != cancellation_input["transfer_id"]
    ):
        raise RepositoryPersistenceError(_ELIGIBILITY_ERROR)

    status_value = transfer.status
    if type(status_value) is not str:
        return _result(
            transfer.id,
            current_status=None,
            blocker=TransferCancellationBlocker.UNKNOWN_OR_MALFORMED_STATUS,
        )
    try:
        current_status = TransferStatus[status_value]
    except KeyError:
        return _result(
            transfer.id,
            current_status=None,
            blocker=TransferCancellationBlocker.UNKNOWN_OR_MALFORMED_STATUS,
        )

    try:
        validate_status_transition(current_status, TransferStatus.CANCELLED)
    except ValueError:
        if current_status is TransferStatus.CANCELLED:
            blocker = TransferCancellationBlocker.ALREADY_CANCELLED
        elif current_status in (TransferStatus.COMPLETED, TransferStatus.REJECTED):
            blocker = TransferCancellationBlocker.TERMINAL_STATUS
        else:
            blocker = TransferCancellationBlocker.POST_DISPATCH_POLICY_UNRESOLVED
        return _result(
            transfer.id,
            current_status=current_status.name,
            blocker=blocker,
        )

    return _result(transfer.id, current_status=current_status.name)


def prepare_transfer_cancellation(
    *,
    actor: AuthenticatedSession,
    cancellation_input: object,
    repository: StockTransferRepository,
) -> NoReturn:
    """Fail closed until a dedicated cancellation permission contract exists.

    No other transfer permission is cancellation authority. This entrypoint
    rejects before validation or repository access and cannot perform writes.
    """
    raise RepositoryAuthorizationError("Permission denied.")
