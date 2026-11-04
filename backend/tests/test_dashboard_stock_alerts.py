"""BE-46 Phase 1 tests: no database, Flask, or stock-policy implementation."""

import copy

import pytest

from dashboard_stock_alerts import prepare_dashboard_stock_alerts


def row(item_id, status, business_id=1, branch_id=2):
    return {
        "business_id": business_id,
        "branch_id": branch_id,
        "item_id": item_id,
        "stock_status": status,
    }


def test_groups_preclassified_alerts():
    result = prepare_dashboard_stock_alerts(
        [row(10, "low"), row(11, "ok"), row(12, "out"), row(13, "low")], 1, 2
    )
    assert result == {
        "business_id": 1,
        "branch_id": 2,
        "low_stock_count": 2,
        "out_of_stock_count": 1,
        "low_stock_items": [{"item_id": 10}, {"item_id": 13}],
        "out_of_stock_items": [{"item_id": 12}],
    }


def test_empty_input_does_not_invent_alerts():
    result = prepare_dashboard_stock_alerts([], 1, 2)
    assert result["low_stock_count"] == result["out_of_stock_count"] == 0
    assert result["low_stock_items"] == result["out_of_stock_items"] == []


def test_tuple_input_and_string_identifiers():
    result = prepare_dashboard_stock_alerts((row("A", "out", "B1", "BR1"),), "B1", "BR1")
    assert result["out_of_stock_items"] == [{"item_id": "A"}]


def test_preserves_original_input():
    original = [row(1, "low"), row(2, "out")]
    before = copy.deepcopy(original)
    result = prepare_dashboard_stock_alerts(original, 1, 2)
    result["low_stock_items"][0]["item_id"] = 99
    assert original == before


def test_deterministic_and_fresh_outputs():
    items = [row(1, "low")]
    a = prepare_dashboard_stock_alerts(items, 1, 2)
    b = prepare_dashboard_stock_alerts(items, 1, 2)
    assert a == b and a is not b
    assert a["low_stock_items"] is not b["low_stock_items"]


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 2.0, [], {}, "", "  "])
def test_invalid_business_scope(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([], value, 2)


@pytest.mark.parametrize("value", [None, True, False, 0, -2, 2.0, [], {}, "", "  "])
def test_invalid_branch_scope(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([], 1, value)


@pytest.mark.parametrize("value", [None, "x", {}, 1, True])
def test_invalid_items_container(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts(value, 1, 2)


@pytest.mark.parametrize("value", [None, 4, [], "test"])
def test_invalid_item(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([value], 1, 2)


@pytest.mark.parametrize("value", [None, True, 0, -1, 1.0, [], "", "  "])
def test_invalid_item_identifier(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([row(value, "low")], 1, 2)


@pytest.mark.parametrize("value", ["LOW", "unknown", "", " low ", None, 1, True, {}, []])
def test_invalid_status(value):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([row(1, value)], 1, 2)


@pytest.mark.parametrize("business,branch", [(3, 2), (1, 3), (True, 2), (1, False), ("1", 2), (1, "2")])
def test_rejects_out_of_scope_items(business, branch):
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([row(1, "low", business, branch)], 1, 2)


def test_one_bad_record_rejects_whole_input():
    with pytest.raises(ValueError):
        prepare_dashboard_stock_alerts([row(1, "low"), row(2, "out", 100, 2)], 1, 2)


def test_no_extra_fields_leak_into_output():
    item = row(3, "out")
    item["private_note"] = "should not be returned"
    result = prepare_dashboard_stock_alerts([item], 1, 2)
    assert "should not be returned" not in str(result)


def test_invalid_data_not_echoed_in_errors():
    item = row(1, "secret-input-value")
    with pytest.raises(ValueError) as exc:
        prepare_dashboard_stock_alerts([item], 1, 2)
    assert "secret-input-value" not in str(exc.value)


def test_no_automatic_business_actions():
    # Pure-function tests: the helper takes supplied rows and does not import
    # database, Flask, Gemini, or a purchase-order service.
    import inspect
    import dashboard_stock_alerts

    source = inspect.getsource(dashboard_stock_alerts)
    for forbidden in ("import flask", "import mysql", "import google", "import requests", "subprocess"):
        assert forbidden not in source.lower()
    assert prepare_dashboard_stock_alerts([row(1, "low")], 1, 2)["low_stock_count"] == 1
