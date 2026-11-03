"""BE-50 Phase 1: CSV generation without Flask, database, or file access.

These column keys are supplied by the caller; they do not define a report schema.
"""

import csv
from io import StringIO
import math


def _csv_cell(value):
    """Accept simple values and protect text intended for spreadsheet viewing."""
    if value is None:
        return ""
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Report numbers must be finite.")
        return value
    if not isinstance(value, str):
        raise ValueError("Report cells must be text, numbers, booleans, or None.")
    if "\x00" in value:
        raise ValueError("Report text must not contain null characters.")

    # Quoting alone does not stop formulas. Inspect whitespace without changing
    # the original text, and include full-width starters used in some locales.
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(
        ("=", "+", "-", "@", "＝", "＋", "－", "＠")
    ):
        return "\t" + value
    return value


def export_report_csv(rows, columns):
    """Return UTF-8 CSV bytes with a BOM, in the supplied column and row order.

    rows must be a list/tuple of dictionaries. columns must be a nonempty
    list/tuple of unique, nonblank string keys chosen by trusted calling code.
    Only selected fields are exported. Missing fields and None become empty
    cells; an empty report still contains the column headers.
    The caller approves the columns; this helper does not decide who may export
    which fields.

    Cells may contain strings, integers, finite floats, booleans, or None. Invalid
    inputs raise ValueError without including report values. Inputs are unchanged.

    All cells are quoted by csv.writer. Formula-like text (including headers)
    receives a tab prefix for spreadsheet viewing, following OWASP's Excel
    guidance: https://community.owasp.org/attacks/CSV_Injection
    This prefix remains part of the CSV data. Spreadsheet import behavior varies;
    consumers must preserve the protection and check their target application.

    Example: export_report_csv([{"label": "Example", "count": 2}],
                               ["count", "label"])
    """
    if not isinstance(rows, (list, tuple)):
        raise ValueError("Report rows must be a list or tuple of dictionaries.")
    if not isinstance(columns, (list, tuple)) or not columns:
        raise ValueError("Report columns must be a nonempty list or tuple.")
    if any(not isinstance(column, str) or not column.strip() for column in columns):
        raise ValueError("Report column names must be nonblank strings.")
    if len(set(columns)) != len(columns):
        raise ValueError("Report column names must be unique.")

    output = StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow([_csv_cell(column) for column in columns])

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each report row must be a dictionary.")
        writer.writerow([_csv_cell(row.get(column)) for column in columns])

    try:
        # The BOM helps spreadsheet programs recognize Arabic and other UTF-8 text.
        return output.getvalue().encode("utf-8-sig")
    except UnicodeEncodeError:
        raise ValueError("Report text must be valid UTF-8.") from None
