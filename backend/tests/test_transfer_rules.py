"""Unit tests for stock-transfer business rules; no database is used."""

import pytest

from transfer_rules import (
    ALLOWED_STATUS_TRANSITIONS,
    TransferStatus,
    calculate_dispatch_quantities,
    calculate_quantity_discrepancies,
    calculate_receipt_quantities,
    validate_branch_ids,
    validate_status_transition,
    validate_transfer,
    validate_transfer_items,
)


VALID_ITEMS = [
    {"product_variant_id": 101, "quantity": 5},
    {"product_variant_id": 202, "quantity": 3},
]


def test_valid_transfer_returns_validated_item_copy():
    original_items = [item.copy() for item in VALID_ITEMS]

    result = validate_transfer(1, 2, original_items)

    assert result == original_items
    assert result is not original_items
    assert result[0] is not original_items[0]
    assert original_items == VALID_ITEMS


@pytest.mark.parametrize(
    ("source_branch_id", "destination_branch_id"),
    [(0, 2), (-1, 2), (1, 0), (1, -2), (True, 2), (1, 2.0), ("1", 2)],
)
def test_branch_ids_must_be_positive_integers(source_branch_id, destination_branch_id):
    with pytest.raises(ValueError):
        validate_branch_ids(source_branch_id, destination_branch_id)


def test_transfer_between_the_same_branch_is_rejected():
    with pytest.raises(ValueError, match="different"):
        validate_transfer(4, 4, VALID_ITEMS)


@pytest.mark.parametrize(
    "item",
    [
        {"product_variant_id": 0, "quantity": 1},
        {"product_variant_id": -1, "quantity": 1},
        {"product_variant_id": True, "quantity": 1},
        {"product_variant_id": 101, "quantity": 0},
        {"product_variant_id": 101, "quantity": -1},
        {"product_variant_id": 101, "quantity": False},
        {"product_variant_id": "101", "quantity": 1},
        {"product_variant_id": 101},
        {"quantity": 1},
        None,
    ],
)
def test_invalid_transfer_item_is_rejected(item):
    with pytest.raises(ValueError):
        validate_transfer_items([item])


def test_empty_transfer_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        validate_transfer_items([])


def test_dispatch_calculation_returns_source_deductions_without_changing_items():
    items = [
        {"product_variant_id": 101, "quantity": 2},
        {"product_variant_id": 101, "quantity": 3},
        {"product_variant_id": 202, "quantity": 4},
    ]
    original_items = [item.copy() for item in items]

    deductions = calculate_dispatch_quantities(items)

    assert deductions == {101: 5, 202: 4}
    assert items == original_items


def test_partial_receipt_adds_only_confirmed_quantities_and_tracks_missing_stock():
    receipt = [{"product_variant_id": 101, "quantity": 2}]

    additions = calculate_receipt_quantities(receipt, VALID_ITEMS)
    discrepancies = calculate_quantity_discrepancies(VALID_ITEMS, receipt)

    assert additions == {101: 2}
    assert discrepancies == {101: 3, 202: 3}


def test_later_receipt_can_resolve_the_remaining_discrepancy():
    dispatched = [{"product_variant_id": 101, "quantity": 5}]
    previous_receipt = [{"product_variant_id": 101, "quantity": 2}]
    next_receipt = [{"product_variant_id": 101, "quantity": 3}]

    additions = calculate_receipt_quantities(
        next_receipt, dispatched, previously_received_items=previous_receipt
    )
    discrepancies = calculate_quantity_discrepancies(dispatched, previous_receipt + next_receipt)

    assert additions == {101: 3}
    assert discrepancies == {}


@pytest.mark.parametrize(
    ("receipt_items", "previously_received_items"),
    [
        ([{"product_variant_id": 999, "quantity": 1}], []),
        ([{"product_variant_id": 101, "quantity": 4}], [{"product_variant_id": 101, "quantity": 2}]),
        ([], []),
    ],
)
def test_invalid_receipt_is_rejected(receipt_items, previously_received_items):
    with pytest.raises(ValueError):
        calculate_receipt_quantities(
            receipt_items,
            [{"product_variant_id": 101, "quantity": 5}],
            previously_received_items=previously_received_items,
        )


def test_received_quantity_cannot_exceed_dispatched_quantity():
    with pytest.raises(ValueError, match="exceed"):
        calculate_quantity_discrepancies(
            [{"product_variant_id": 101, "quantity": 2}],
            [{"product_variant_id": 101, "quantity": 3}],
        )


def test_statuses_and_allowed_transitions_are_defined():
    assert {status.value for status in TransferStatus} == {
        "Pending",
        "In Transit",
        "Completed",
        "Rejected",
        "Cancelled",
    }
    assert ALLOWED_STATUS_TRANSITIONS[TransferStatus.PENDING] == {
        TransferStatus.IN_TRANSIT,
        TransferStatus.REJECTED,
        TransferStatus.CANCELLED,
    }
    assert ALLOWED_STATUS_TRANSITIONS[TransferStatus.IN_TRANSIT] == {
        TransferStatus.COMPLETED
    }


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (TransferStatus.PENDING, TransferStatus.IN_TRANSIT),
        (TransferStatus.PENDING, TransferStatus.REJECTED),
        (TransferStatus.PENDING, TransferStatus.CANCELLED),
    ],
)
def test_valid_pending_status_transitions(current, new):
    validate_status_transition(current, new)


def test_transfer_can_be_completed_when_everything_was_received():
    items = [{"product_variant_id": 101, "quantity": 5}]

    validate_status_transition(
        TransferStatus.IN_TRANSIT,
        TransferStatus.COMPLETED,
        dispatched_items=items,
        received_items=items,
    )


def test_transfer_cannot_be_completed_without_confirmed_receipt_items():
    with pytest.raises(ValueError, match="at least one"):
        validate_status_transition(
            TransferStatus.IN_TRANSIT,
            TransferStatus.COMPLETED,
            dispatched_items=[{"product_variant_id": 101, "quantity": 5}],
            received_items=[],
        )


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (TransferStatus.PENDING, TransferStatus.COMPLETED),
        (TransferStatus.PENDING, TransferStatus.PENDING),
        (TransferStatus.IN_TRANSIT, TransferStatus.CANCELLED),
        (TransferStatus.COMPLETED, TransferStatus.IN_TRANSIT),
        (TransferStatus.REJECTED, TransferStatus.IN_TRANSIT),
        (TransferStatus.CANCELLED, TransferStatus.PENDING),
        ("Unknown", TransferStatus.PENDING),
    ],
)
def test_invalid_status_transition_is_rejected(current, new):
    with pytest.raises(ValueError):
        validate_status_transition(current, new)


def test_transfer_can_be_completed_with_partial_receipt_and_open_discrepancy():
    dispatched = [{"product_variant_id": 101, "quantity": 5}]
    received = [{"product_variant_id": 101, "quantity": 4}]

    validate_status_transition(
        TransferStatus.IN_TRANSIT,
        TransferStatus.COMPLETED,
        dispatched_items=dispatched,
        received_items=received,
    )

    assert calculate_receipt_quantities(received, dispatched) == {101: 4}
    assert calculate_quantity_discrepancies(dispatched, received) == {101: 1}


def test_repeated_completion_is_rejected_after_receipt_was_confirmed():
    dispatched = [{"product_variant_id": 101, "quantity": 5}]
    received = [{"product_variant_id": 101, "quantity": 4}]

    with pytest.raises(ValueError, match="Cannot change"):
        validate_status_transition(
            TransferStatus.COMPLETED,
            TransferStatus.COMPLETED,
            dispatched_items=dispatched,
            received_items=received,
        )


def test_repeated_dispatch_transition_is_rejected():
    with pytest.raises(ValueError, match="Cannot change"):
        validate_status_transition(
            TransferStatus.IN_TRANSIT,
            TransferStatus.IN_TRANSIT,
        )
