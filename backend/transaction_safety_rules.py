"""Pure request-idempotency and inventory-lock ordering contracts.

These helpers validate trusted request identity and produce deterministic
decisions. They do not reserve keys, coordinate processes, lock rows, or make
database writes. MySQL must enforce the same contract transactionally.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math

from permissions import is_trusted_principal


class TransactionOperation(str, Enum):
    """Inventory or sales operation scopes covered by this contract."""

    SALE = "sale"
    PURCHASE_RECEIVING = "purchase_receiving"
    MANUAL_STOCK_ADJUSTMENT = "manual_stock_adjustment"
    APPROVED_SALE_CANCELLATION = "approved_sale_cancellation"
    RETURN_SUBMISSION = "return_submission"
    RETURN_INSPECTION = "return_inspection"
    TRANSFER_DISPATCH = "transfer_dispatch"
    TRANSFER_RECEIVING = "transfer_receiving"


class IdempotencyState(str, Enum):
    """Durable request states understood by the pure decision helper."""

    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"


class IdempotencyDecision(str, Enum):
    """Action the persistence adapter must take for an idempotency lookup."""

    PROCEED = "proceed"
    REPLAY_COMMITTED_RESULT = "replay_committed_result"
    REQUEST_IN_PROGRESS = "request_in_progress"
    REJECT_PAYLOAD_CONFLICT = "reject_payload_conflict"


@dataclass(frozen=True)
class IdempotencyRequest:
    """Validated identity for one operation request; contains no raw payload."""

    operation_type: TransactionOperation
    business_id: int
    request_key: str
    payload_hash: str

    @property
    def scope_key(self):
        """The unique request scope: business, operation, and client key."""
        return (self.business_id, self.operation_type, self.request_key)


@dataclass(frozen=True)
class ExistingIdempotencyRecord:
    """Identity and state loaded from trusted persistence; result stays separate."""

    request: IdempotencyRequest
    state: IdempotencyState


def _normalize_operation(operation_type):
    if type(operation_type) is TransactionOperation:
        return operation_type
    if type(operation_type) is str:
        try:
            return TransactionOperation(operation_type)
        except ValueError:
            pass
    raise ValueError("operation_type is not supported.")


def _validate_request_key(request_key):
    if (
        type(request_key) is not str
        or not request_key
        or len(request_key) > 255
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in request_key)
    ):
        raise ValueError(
            "request_key must contain 1 to 255 visible ASCII characters without whitespace."
        )
    return request_key


def _canonical_json_value(value):
    """Copy a JSON value while rejecting types with ambiguous encodings."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("payload must contain only finite JSON numbers.")
        return value
    if isinstance(value, Mapping):
        normalized = {}
        for key, nested in value.items():
            if type(key) is not str:
                raise ValueError("payload object keys must be strings.")
            normalized[key] = _canonical_json_value(nested)
        return normalized
    if type(value) is list:
        return [_canonical_json_value(nested) for nested in value]
    raise ValueError("payload must contain JSON-compatible values only.")


def canonical_payload_hash(payload):
    """Return a SHA-256 identity for a JSON object without returning its data.

    Object keys are sorted recursively and insignificant JSON whitespace is
    removed. The versioned prefix allows a future canonicalization change to
    use a distinct hash domain. The digest is an identifier, not encryption.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object.")
    normalized = _canonical_json_value(payload)
    canonical_json = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(b"branchwise-idempotency-payload-v1\x00" + canonical_json)
    return digest.hexdigest()


def create_idempotency_request(
    principal, operation_type, request_key, payload
):
    """Validate request identity and derive business scope from a trusted user.

    No business ID is accepted as a request argument: this contract takes it
    only from the already authenticated server-side principal. This check does
    not replace operation permission or resource-scope validation.
    """
    if not is_trusted_principal(principal):
        raise PermissionError("A trusted authenticated business identity is required.")
    operation = _normalize_operation(operation_type)
    key = _validate_request_key(request_key)
    return IdempotencyRequest(
        operation_type=operation,
        business_id=principal.business_id,
        request_key=key,
        payload_hash=canonical_payload_hash(payload),
    )


def _validate_idempotency_request(request):
    if type(request) is not IdempotencyRequest:
        raise ValueError("A validated idempotency request is required.")
    if type(request.operation_type) is not TransactionOperation:
        raise ValueError("Stored operation_type is invalid.")
    if type(request.business_id) is not int or request.business_id <= 0:
        raise ValueError("Stored business_id must be a positive integer.")
    _validate_request_key(request.request_key)
    if (
        type(request.payload_hash) is not str
        or len(request.payload_hash) != 64
        or any(character not in "0123456789abcdef" for character in request.payload_hash)
    ):
        raise ValueError("Stored payload hash is invalid.")


def decide_idempotency(request, existing_record=None):
    """Choose whether to proceed, replay, wait, or reject a scoped key reuse.

    A record from another operation or business is outside this request's key
    scope and therefore does not block it. A record for the same scope with a
    different payload is a conflict regardless of its state.
    """
    _validate_idempotency_request(request)
    if existing_record is None:
        return IdempotencyDecision.PROCEED
    if type(existing_record) is not ExistingIdempotencyRecord:
        raise ValueError("existing_record must be trusted persisted request state.")
    _validate_idempotency_request(existing_record.request)
    if type(existing_record.state) is not IdempotencyState:
        raise ValueError("Stored idempotency state is invalid.")

    if existing_record.request.scope_key != request.scope_key:
        return IdempotencyDecision.PROCEED
    if existing_record.request.payload_hash != request.payload_hash:
        return IdempotencyDecision.REJECT_PAYLOAD_CONFLICT
    if existing_record.state is IdempotencyState.IN_PROGRESS:
        return IdempotencyDecision.REQUEST_IN_PROGRESS
    return IdempotencyDecision.REPLAY_COMMITTED_RESULT


def ordered_inventory_balance_keys(balance_keys):
    """Validate, deduplicate, and sort (business, branch, variant) lock keys.

    Callers derive the keys from an already validated movement plan, then
    acquire every corresponding database row lock in the returned order.
    """
    if isinstance(balance_keys, (str, bytes, Mapping)) or not isinstance(
        balance_keys, Iterable
    ):
        raise ValueError("balance_keys must be an iterable of three-ID keys.")

    normalized = set()
    for key in balance_keys:
        if not isinstance(key, (tuple, list)) or len(key) != 3:
            raise ValueError(
                "Each balance key must contain business, branch, and variant IDs."
            )
        if any(type(value) is not int or value <= 0 for value in key):
            raise ValueError("Balance key IDs must be positive integers.")
        normalized.add(tuple(key))
    return tuple(sorted(normalized))


__all__ = [
    "ExistingIdempotencyRecord",
    "IdempotencyDecision",
    "IdempotencyRequest",
    "IdempotencyState",
    "TransactionOperation",
    "canonical_payload_hash",
    "create_idempotency_request",
    "decide_idempotency",
    "ordered_inventory_balance_keys",
]
