from copy import deepcopy
from decimal import Decimal

import pytest

from branchwise.transfers.transfer_request_validation import validate_transfer_request_input as validate


def request():
    return dict(source_branch_id=1, destination_branch_id=2,
                items=[dict(product_variant_id=3, quantity=1)])


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 1.0, '1', [], {}, Decimal('1')])
@pytest.mark.parametrize('field', ['source_branch_id', 'destination_branch_id', 'product_variant_id', 'quantity'])
def test_invalid_numbers(value, field):
    data = request()
    target = data if field.endswith('branch_id') else data['items'][0]
    target[field] = value
    assert validate(data)


@pytest.mark.parametrize('data', [None, [], (), '', 1, True])
def test_invalid_body(data):
    assert 'body' in validate(data)


@pytest.mark.parametrize('items', [None, [], (), {}, 'items', [None], [[]], [1]])
def test_invalid_items(items):
    data = request()
    data['items'] = items
    assert validate(data)


@pytest.mark.parametrize('field', ['source_branch_id', 'destination_branch_id', 'items', 'product_variant_id', 'quantity'])
def test_missing(field):
    data = request()
    target = data if field in data else data['items'][0]
    del target[field]
    assert validate(data)


@pytest.mark.parametrize('field', ['user_id', 'requested_by', 'business_id', 'status', 'reference', 'created_at', 'timestamp', 'permissions', 'role', 'stock', 'unknown', 7])
@pytest.mark.parametrize('level', ['root', 'item'])
def test_unknown_fields(field, level):
    data = request()
    target = data if level == 'root' else data['items'][0]
    target[field] = None
    assert validate(data)


def test_same_branch():
    data = request()
    data['destination_branch_id'] = 1
    assert 'destination_branch_id' in validate(data)


@pytest.mark.parametrize('invalid', [False, True])
def test_immutability(invalid):
    data = request()
    data['items'].append(dict(product_variant_id=4, quantity=10**100))
    if invalid:
        data['items'].append({'permissions': {'nested': [1, 2]}})
    before = deepcopy(data)
    errors = validate(data)
    assert bool(errors) is invalid
    assert data == before


def test_valid_and_duplicate_policy_unresolved():
    data = request()
    assert validate(data) == {}
    data['items'].append(dict(data['items'][0]))
    assert validate(data) == {}
