"""Unit tests for the BE-44 Phase 1 decision-input helper."""

import builtins
import socket
import sqlite3
from pathlib import Path
from urllib import request

import pytest

from decision_input import prepare_decision_input


@pytest.mark.parametrize("decision", ["accepted", "rejected", "deferred"])
def test_accepts_all_approved_decisions(decision):
    assert prepare_decision_input(decision) == {
        "decision": decision,
        "reason": None,
    }


@pytest.mark.parametrize("reason", [None, "", " ", "\t\n", "Stock is adequate", "نؤجل القرار", "  Keep spaces  "])
def test_preserves_valid_optional_reasons(reason):
    assert prepare_decision_input("deferred", reason) == {
        "decision": "deferred",
        "reason": reason,
    }


@pytest.mark.parametrize(
    "decision",
    [
        None,
        "",
        " ",
        "Accepted",
        "ACCEPTED",
        "accepted ",
        " rejected",
        "pending",
        "saved",
        "approved",
        "unknown confidential decision",
        0,
        1,
        False,
        True,
        1.5,
        [],
        ["accepted"],
        {},
        {"decision": "accepted"},
        ("accepted",),
    ],
)
def test_rejects_invalid_decisions_without_leaking_values(decision):
    with pytest.raises(ValueError) as exc:
        prepare_decision_input(decision)
    assert str(exc.value) == "Decision must be accepted, rejected, or deferred."
    assert "confidential" not in str(exc.value)


@pytest.mark.parametrize(
    "reason",
    [
        0,
        1,
        -1,
        True,
        False,
        3.25,
        [],
        ["confidential"],
        {},
        {"secret": "confidential"},
        ("reason",),
        b"confidential",
    ],
)
def test_rejects_invalid_reason_types_with_safe_error(reason):
    with pytest.raises(ValueError) as exc:
        prepare_decision_input("accepted", reason)
    assert str(exc.value) == "Reason must be text or None."
    assert "confidential" not in str(exc.value)


def test_decision_is_checked_before_reason():
    with pytest.raises(ValueError, match="^Decision must be accepted, rejected, or deferred\\.$"):
        prepare_decision_input("invalid", 123)


def test_fresh_result_dictionaries():
    first = prepare_decision_input("accepted", "same reason")
    second = prepare_decision_input("accepted", "same reason")
    assert first == second
    assert first is not second
    first["reason"] = "changed"
    assert second["reason"] == "same reason"


def test_output_has_only_decision_and_reason():
    assert set(prepare_decision_input("rejected")) == {"decision", "reason"}


def test_deterministic_output():
    expected = {"decision": "deferred", "reason": " Later "}
    for _ in range(5):
        assert prepare_decision_input("deferred", " Later ") == expected


def test_does_not_access_files_database_or_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("BE-44 input preparation must not access external resources")

    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(Path, "open", blocked)
    monkeypatch.setattr(sqlite3, "connect", blocked)
    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(request, "urlopen", blocked)

    assert prepare_decision_input("accepted", "OK") == {
        "decision": "accepted",
        "reason": "OK",
    }
