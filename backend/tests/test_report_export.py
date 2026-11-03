"""In-memory CSV export checks using generic report data only."""

import builtins
import codecs
import csv
import io
import socket
import sqlite3
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest

from report_export import export_report_csv


@pytest.fixture(autouse=True)
def blocked_external_services(monkeypatch):
    """Fail on network or database access, even if the helper catches it."""
    attempted_access = Mock(side_effect=AssertionError("External access attempted"))
    monkeypatch.setattr(socket, "create_connection", attempted_access)
    monkeypatch.setattr(socket, "getaddrinfo", attempted_access)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, attempted_access)
    monkeypatch.setattr(sqlite3, "connect", attempted_access)

    yield attempted_access

    attempted_access.assert_not_called()


def read_csv(payload):
    return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))


def test_arabic_export_has_bom_and_preserves_selected_column_and_row_order():
    rows = [
        {"label": "الفرع الأول", "count": 2, "private": "excluded-value"},
        {"label": "الفرع الثاني", "count": 3, "extra": {"not": "exportable"}},
    ]

    payload = export_report_csv(rows, ["count", "label"])

    assert isinstance(payload, bytes)
    assert payload.startswith(codecs.BOM_UTF8)
    assert read_csv(payload) == [
        ["count", "label"],
        ["2", "الفرع الأول"],
        ["3", "الفرع الثاني"],
    ]
    assert b"excluded-value" not in payload


def test_arabic_column_names_and_unicode_values_round_trip():
    rows = [{"الوصف": "تقرير 📊", "العدد": 4}]

    payload = export_report_csv(rows, ["العدد", "الوصف"])

    assert read_csv(payload) == [["العدد", "الوصف"], ["4", "تقرير 📊"]]


@pytest.mark.parametrize("rows", [[], ()], ids=["list", "tuple"])
def test_empty_rows_produce_only_the_quoted_header(rows):
    payload = export_report_csv(rows, ["label", "count"])

    assert payload.decode("utf-8-sig") == '"label","count"\r\n'


def test_tuple_rows_and_columns_are_supported_without_trimming_keys():
    payload = export_report_csv(
        ({" label ": "first", "label": "different"},), (" label ",)
    )

    assert read_csv(payload) == [[" label "], ["first"]]


def test_missing_none_and_empty_values_are_empty_cells():
    payload = export_report_csv(
        [{"label": "present"}, {"label": None}, {"label": ""}, {}], ["label"]
    )

    assert read_csv(payload) == [["label"], ["present"], [""], [""], [""]]


def test_supported_scalars_preserve_zero_false_and_numeric_negatives():
    payload = export_report_csv(
        [{"zero": 0, "integer": -12, "float": -12.5, "yes": True, "no": False}],
        ["zero", "integer", "float", "yes", "no"],
    )

    assert read_csv(payload) == [
        ["zero", "integer", "float", "yes", "no"],
        ["0", "-12", "-12.5", "True", "False"],
    ]


def test_quotes_separators_and_embedded_newlines_cannot_create_extra_cells():
    column = 'label,"quoted"\r\ncontinued'
    value = 'ordinary text",=1+1;next\r\nline "two"'

    payload = export_report_csv([{column: value, "empty": ""}], [column, "empty"])

    assert read_csv(payload) == [[column, "empty"], [value, ""]]
    text = payload.decode("utf-8-sig")
    assert '""quoted""' in text
    assert '""two""' in text
    assert text.endswith(',""\r\n')


@pytest.mark.parametrize(
    "text",
    [
        "=1+1",
        "+1",
        "-12",
        "@SUM(A1:A2)",
        "  =1+1",
        "\tordinary text",
        "\rordinary text",
        "\nordinary text",
        " \t=1+1",
        "＝1+1",
        "＋1",
        "－1",
        "＠SUM(A1:A2)",
    ],
    ids=[
        "equals", "plus", "numeric-text", "at", "spaces", "tab", "cr", "lf",
        "mixed-whitespace", "fullwidth-equals", "fullwidth-plus",
        "fullwidth-minus", "fullwidth-at",
    ],
)
def test_dangerous_text_headers_and_values_receive_a_literal_tab(text):
    payload = export_report_csv([{text: text}], [text])

    assert read_csv(payload) == [["\t" + text], ["\t" + text]]
    assert payload.decode("utf-8-sig").startswith('"\t')


def test_formula_payload_with_quotes_and_newlines_stays_one_literal_cell():
    text = '=1+1","=2+2\r\n=3+3'

    payload = export_report_csv([{text: text}], [text])

    assert read_csv(payload) == [["\t" + text], ["\t" + text]]
    assert '""' in payload.decode("utf-8-sig")


@pytest.mark.parametrize(
    "text", ["ordinary text", "'=1+1", "part=1", "  plain text  ", "مرحبا"]
)
def test_benign_text_is_preserved(text):
    assert read_csv(export_report_csv([{text: text}], [text])) == [[text], [text]]


@pytest.mark.parametrize(
    "rows",
    [None, "private-value", {"private-key": "private-value"}, iter([]), [None], ["row"]],
    ids=["none", "string", "mapping", "iterator", "null-row", "string-row"],
)
def test_invalid_row_containers_and_rows_raise_safe_value_error(rows):
    with pytest.raises(ValueError) as error:
        export_report_csv(rows, ["label"])

    assert "private-value" not in str(error.value)
    assert "private-key" not in str(error.value)


@pytest.mark.parametrize(
    "columns",
    [
        None,
        "label",
        {"label"},
        {"label": None},
        [],
        (),
        [""],
        [" \t "],
        ["label", "label"],
        ["label", 1],
        ["private-header\0"],
        ["\ud800"],
    ],
    ids=[
        "none", "string", "set", "mapping", "empty-list", "empty-tuple",
        "empty-name", "blank-name", "duplicate", "nontext-name", "nul", "surrogate",
    ],
)
def test_invalid_columns_raise_safe_value_error(columns):
    with pytest.raises(ValueError) as error:
        export_report_csv([], columns)

    assert "private-header" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        b"private-value",
        ["private-value"],
        {"private": "private-value"},
        {"private-value"},
        object(),
        Decimal("1.25"),
        date(2026, 1, 1),
        float("nan"),
        float("inf"),
        float("-inf"),
        "private-value\0",
        "private-value\ud800",
    ],
    ids=[
        "bytes", "list", "mapping", "set", "object", "decimal", "date",
        "nan", "positive-infinity", "negative-infinity", "nul", "surrogate",
    ],
)
def test_unsupported_selected_values_raise_safe_value_error(value):
    with pytest.raises(ValueError) as error:
        export_report_csv([{"private-field": value}], ["private-field"])

    assert "private-field" not in str(error.value)
    assert "private-value" not in str(error.value)


def test_export_does_not_change_rows_columns_or_nested_unselected_values():
    rows = [{"label": "=1+1", "optional": None, "extra": {"nested": [1, 2]}}]
    columns = ["label", "optional"]
    original_rows = deepcopy(rows)
    original_columns = list(columns)

    payload = export_report_csv(rows, columns)

    assert read_csv(payload) == [["label", "optional"], ["\t=1+1", ""]]
    assert rows == original_rows
    assert columns == original_columns


def test_export_uses_no_files(monkeypatch):
    attempted_file_access = Mock(side_effect=AssertionError("File access attempted"))

    with monkeypatch.context() as file_guard:
        file_guard.setattr(builtins, "open", attempted_file_access)
        file_guard.setattr(io, "open", attempted_file_access)
        payload = export_report_csv([{"label": "local"}], ["label"])

    assert read_csv(payload) == [["label"], ["local"]]
    attempted_file_access.assert_not_called()
