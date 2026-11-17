"""Tests for pure transaction-safety contracts; no database is used."""

from dataclasses import replace

import pytest

from permissions import AuthenticatedPrincipal, Role
from transaction_safety_rules import (
    ExistingIdempotencyRecord,
    IdempotencyDecision,
    IdempotencyState,
    TransactionOperation,
    canonical_payload_hash,
    create_idempotency_request,
    decide_idempotency,
    ordered_inventory_balance_keys,
)


def principal(business_id=10):
    return AuthenticatedPrincipal(
        role=Role.OWNER,
        business_id=business_id,
        branch_id=None,
        permissions=frozenset(),
    )


def request(*, operation=TransactionOperation.SALE, key="retry-001", payload=None, business_id=10):
    if payload is None:
        payload = {"items": [{"variant_id": 101, "quantity": 2}]}
    return create_idempotency_request(
        principal(business_id), operation, key, payload
    )


@pytest.mark.parametrize("operation", ["unknown", "", None, 12])
def test_rejects_unknown_operation_types(operation):
    with pytest.raises(ValueError, match="operation_type"):
        request(operation=operation)


@pytest.mark.parametrize("key", [None, "", " has-space", "has-space ", "snowman-☃", "x" * 256])
def test_rejects_invalid_request_keys(key):
    with pytest.raises(ValueError, match="request_key"):
        request(key=key)


def test_request_uses_business_identity_from_trusted_principal():
    prepared = request(business_id=42)

    assert prepared.business_id == 42
    assert prepared.scope_key == (42, TransactionOperation.SALE, "retry-001")
    assert len(prepared.payload_hash) == 64


@pytest.mark.parametrize("untrusted", [None, object(), {"business_id": 42}])
def test_rejects_untrusted_business_identity(untrusted):
    with pytest.raises(PermissionError, match="trusted authenticated"):
        create_idempotency_request(
            untrusted, TransactionOperation.SALE, "retry-001", {"amount": 1}
        )


def test_payload_hash_is_stable_for_mapping_order_and_nested_json():
    left = {"customer": {"phone": "555-0100", "name": "Ali"}, "items": [1, 2]}
    right = {"items": [1, 2], "customer": {"name": "Ali", "phone": "555-0100"}}

    assert canonical_payload_hash(left) == canonical_payload_hash(right)
    assert canonical_payload_hash(left) != canonical_payload_hash(
        {"items": [2, 1], "customer": {"name": "Ali", "phone": "555-0100"}}
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {1: "non-string key"},
        {"number": float("nan")},
        {"number": float("inf")},
        {"unsupported": object()},
    ],
)
def test_rejects_noncanonical_or_non_json_payload_values(payload):
    with pytest.raises(ValueError, match="payload"):
        canonical_payload_hash(payload)


def test_first_request_proceeds_and_same_committed_request_replays():
    prepared = request()
    assert decide_idempotency(prepared) is IdempotencyDecision.PROCEED

    committed = ExistingIdempotencyRecord(prepared, IdempotencyState.COMMITTED)
    assert decide_idempotency(prepared, committed) is (
        IdempotencyDecision.REPLAY_COMMITTED_RESULT
    )


def test_same_key_and_scope_with_different_payload_is_rejected():
    existing = ExistingIdempotencyRecord(
        request(), IdempotencyState.COMMITTED
    )
    changed = request(payload={"items": [{"variant_id": 101, "quantity": 3}]})

    assert decide_idempotency(changed, existing) is (
        IdempotencyDecision.REJECT_PAYLOAD_CONFLICT
    )


def test_same_key_with_different_payload_is_rejected_while_request_is_in_progress():
    existing = ExistingIdempotencyRecord(request(), IdempotencyState.IN_PROGRESS)
    changed = request(payload={"items": [{"variant_id": 101, "quantity": 3}]})

    assert decide_idempotency(changed, existing) is (
        IdempotencyDecision.REJECT_PAYLOAD_CONFLICT
    )


def test_same_key_and_payload_still_cannot_execute_while_request_is_in_progress():
    prepared = request()
    processing = ExistingIdempotencyRecord(prepared, IdempotencyState.IN_PROGRESS)

    assert decide_idempotency(prepared, processing) is (
        IdempotencyDecision.REQUEST_IN_PROGRESS
    )


def test_malformed_persisted_idempotency_record_fails_closed():
    prepared = request()

    with pytest.raises(ValueError, match="Stored idempotency state"):
        decide_idempotency(
            prepared,
            ExistingIdempotencyRecord(prepared, "committed"),
        )

    malformed_request = replace(prepared, payload_hash="not-a-sha256-hash")
    with pytest.raises(ValueError, match="Stored payload hash"):
        decide_idempotency(
            prepared,
            ExistingIdempotencyRecord(malformed_request, IdempotencyState.COMMITTED),
        )


@pytest.mark.parametrize(
    "different_scope",
    [
        {"operation": TransactionOperation.TRANSFER_DISPATCH},
        {"business_id": 11},
    ],
)
def test_same_key_in_another_operation_or_business_has_a_distinct_scope(different_scope):
    existing_request = request()
    other_request = request(**different_scope)

    assert existing_request.request_key == other_request.request_key
    assert decide_idempotency(
        other_request,
        ExistingIdempotencyRecord(existing_request, IdempotencyState.COMMITTED),
    ) is IdempotencyDecision.PROCEED


def test_inventory_balance_locks_have_stable_sorted_deduplicated_order():
    unordered = [(10, 22, 102), (10, 21, 101), (10, 22, 101), (10, 21, 101)]

    assert ordered_inventory_balance_keys(unordered) == (
        (10, 21, 101),
        (10, 22, 101),
        (10, 22, 102),
    )
    assert ordered_inventory_balance_keys(reversed(unordered)) == (
        (10, 21, 101),
        (10, 22, 101),
        (10, 22, 102),
    )


@pytest.mark.parametrize("keys", ["not-keys", [(10, 22)], [(10, 22, True)]])
def test_rejects_invalid_inventory_balance_lock_keys(keys):
    with pytest.raises(ValueError, match="(?i)balance key|balance_keys"):
        ordered_inventory_balance_keys(keys)
