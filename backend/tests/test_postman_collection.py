"""Offline contract checks for the BE-57 collection against real Flask routes."""

import json
from pathlib import Path
import re

import pytest
from flask import Flask
from app import app
from branchwise.suppliers.supplier_routes import supplier_bp
from branchwise.purchasing.purchase_order_routes import purchase_order_bp
from branchwise.purchasing.purchase_receiving_routes import purchase_receiving_bp


PATH = Path(__file__).resolve().parents[1] / 'postman/BranchWise.postman_collection.json'
COLLECTION = json.loads(PATH.read_text(encoding='utf-8'))
PROVISIONAL = COLLECTION['item'][1]


def test_collection_structure_and_safe_defaults():
    assert COLLECTION['info']['schema'] == 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'
    assert COLLECTION['variable'] == [{'key': 'base_url', 'value': 'http://127.0.0.1:5000', 'type': 'string'}]
    assert len(COLLECTION['item']) == 2
    assert len(PROVISIONAL['item']) == 6
    assert 'NOT REGISTERED / NOT IMPLEMENTED' in PROVISIONAL['name']
    assert 'pm.execution.skipRequest();' in PROVISIONAL['event'][0]['script']['exec']
    assert PROVISIONAL['event'][0]['listen'] == 'prerequest'


def test_registered_request_matches_live_app():
    request = COLLECTION['item'][0]['item'][0]['request']
    assert request['method'] == 'GET'
    assert request['url'] == '{{base_url}}/api/health'
    response = app.test_client().get('/api/health')
    assert response.status_code == 200
    assert response.json == {'status': 'ok'}


@pytest.mark.parametrize('entry', PROVISIONAL['item'])
def test_provisional_examples_match_existing_blueprints(entry):
    isolated = Flask(__name__)
    for blueprint in (supplier_bp, purchase_order_bp, purchase_receiving_bp):
        isolated.register_blueprint(blueprint)
    request = entry['request']
    assert 'NOT REGISTERED / NOT IMPLEMENTED' in entry['name']
    assert request['method'] == 'POST'
    path = request['url'].removeprefix('{{base_url}}')
    assert path in {'/api/suppliers', '/api/purchase-orders', '/api/purchase-receiving'}
    assert app.test_client().post(path, json={}).status_code == 404
    body = json.loads(request['body']['raw'])
    response = isolated.test_client().post(path, json=body)
    expected = 400 if 'Invalid structure' in entry['name'] else 501
    assert response.status_code == expected
    assert ('errors' if expected == 400 else 'message') in response.json


def test_no_credentials_or_external_request_targets():
    text = PATH.read_text(encoding='utf-8')
    assert not re.search(r'-----BEGIN .*PRIVATE KEY|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}', text)
    for folder in COLLECTION['item']:
        for entry in folder['item']:
            request = entry['request']
            assert request['url'].startswith('{{base_url}}/api/')
            assert 'auth' not in request
            assert all(h['key'].lower() not in {'authorization', 'cookie', 'x-api-key'} for h in request['header'])
    assert 'auth' not in COLLECTION
