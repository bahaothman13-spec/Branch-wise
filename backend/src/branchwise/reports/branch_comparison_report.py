"""BE-49 Phase 1: prepare already calculated, authorized branch sales summaries.

PROVISIONAL internal data contract, not a reporting API or database schema.
A trusted service must authenticate the user and authorize access to the business
and every participating branch before calling this helper. Matching identifiers,
permission flags, or report-definition metadata do not establish authorization,
source completeness, or approved sales eligibility rules.

BE-47 owns sales calculations. This helper performs no sales arithmetic, growth,
ranking, inventory comparison, cross-business comparison, or external access.
"""

from datetime import date
from decimal import Decimal
import re


def _read_identifier(value, name):
    """Use BE-47's positive built-in int/nonblank built-in str convention."""
    if type(value) not in (int, str):
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} must be a valid identifier.")
    return value


def _read_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Report dates must use YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Report dates must be valid calendar dates.") from None


def _read_label(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Report context labels must be nonblank text.")
    return value


def _read_definition_context(value):
    """Copy opaque caller annotations; do not interpret their factual meaning."""
    if not isinstance(value, dict) or not value:
        raise ValueError("Report-definition context must be a nonempty text dictionary.")
    for key, annotation in value.items():
        if (
            not isinstance(key, str) or not key.strip()
            or not isinstance(annotation, str) or not annotation.strip()
        ):
            raise ValueError("Report-definition context keys and values must be nonblank text.")
    return value.copy()


def _read_reporting_context(report):
    start = _read_date(report.get("start_date"))
    end = _read_date(report.get("end_date"))
    if start > end:
        raise ValueError("Report start_date must not follow end_date.")
    return {
        "start_date": report["start_date"],
        "end_date": report["end_date"],
        "currency": _read_label(report.get("currency")),
        "quantity_unit": _read_label(report.get("quantity_unit")),
        "sales_value_basis": _read_label(report.get("sales_value_basis")),
    }


def _read_summary(value):
    """Copy the existing BE-47 summary fields without recomputing any value."""
    if not isinstance(value, dict):
        raise ValueError("Each report must contain a summary dictionary.")
    count = value.get("transaction_count")
    quantity = value.get("total_quantity")
    money = value.get("total_sales_value")
    if type(count) is not int or count < 0:
        raise ValueError("Transaction count must be a nonnegative built-in integer.")
    if type(quantity) is not int:
        raise ValueError("Total quantity must be a built-in integer.")
    if not isinstance(money, Decimal) or not money.is_finite():
        raise ValueError("Total sales value must be a finite Decimal.")
    return {
        "transaction_count": count,
        "total_quantity": quantity,
        "total_sales_value": money,
    }


def prepare_branch_sales_comparison(
    branch_reports, *, business_id, report_definition_context
):
    """Validate and copy compatible sales summaries for branches of one business.

    PROVISIONAL input contract:
    - branch_reports is a list/tuple of wrapper dictionaries. Each wrapper has
      ``report`` (an already calculated BE-47 report) and a separate
      ``report_definition_context``. This adds no fields to BE-47's output.
    - report requires BE-47's business_id, branch_id, start_date, end_date,
      currency, quantity_unit, sales_value_basis, and summary fields. The three
      summary values are transaction_count (nonnegative built-in int),
      total_quantity (signed built-in int), and total_sales_value (finite
      Decimal). Do not convert, round, or recompute them. Calculated totals are
      not restricted to BE-47's individual observation money-size bounds.
    - IDs are positive built-in integers or nonblank built-in strings, with no
      coercion. Every business_id must match the explicit requested business by
      type and value. Branch IDs must be distinct, even for identical reports.
      Integer 1 and string '1' are separate identifiers; booleans are invalid.
    - All reports require identical normalized YYYY-MM-DD start/end dates
      (start <= end), currency, quantity unit, and sales value basis. Labels are
      explicit nonblank strings and match exactly, without normalization.
      Mismatched date ranges are rejected, never re-filtered after aggregation.
    - The explicit report_definition_context argument and every wrapper's
      context must be equal nonempty dictionaries of nonblank string keys and
      values. For example, {'definition': 'caller-defined-sales-v1'}. Dictionary
      key order is irrelevant; no annotation has special meaning here.
      These are consistency checks only. The caller owns the actual definition,
      timezone attribution, filters, eligibility rules, and comparable coverage.
    - Products and unknown fields in reports, summaries, and wrappers are not
      used or returned. No product totals or relationships between summary
      values are checked; this helper does not verify BE-47's calculations.
      Permission, status, or ranking annotations cannot decide inclusion.

    PROVISIONAL output contract:
    Return business_id, a fresh report_definition_context dictionary, and
    branches. Each branch has branch_id, the five existing reporting context
    fields, and a fresh summary dictionary with the three unchanged values.
    Sort by branch ID, integers first, then strings, ascending within each type.
    This is identifier ordering, not a performance ranking.
    Empty input returns branches=[]; missing branches never become zero rows.
    Supplied zeros remain unchanged and do not establish source completeness.

    Invalid input raises ValueError without supplied values or partial results.
    Inputs stay unchanged and output containers are fresh. Decimal objects are
    immutable and retained exactly, including their sign, exponent, and zeros.
    No differences, percentages, scores, selection of winners, or combined
    totals are produced. Real authorization remains outside this helper.
    """
    _read_identifier(business_id, "business_id")
    definition = _read_definition_context(report_definition_context)
    if not isinstance(branch_reports, (list, tuple)):
        raise ValueError("Branch reports must be a list or tuple.")

    branches = []
    branch_ids_seen = set()
    common_context = None
    for entry in branch_reports:
        if not isinstance(entry, dict) or not isinstance(entry.get("report"), dict):
            raise ValueError("Each branch entry must contain a report dictionary.")
        report = entry["report"]
        actual_business = _read_identifier(report.get("business_id"), "business_id")
        if type(actual_business) is not type(business_id) or actual_business != business_id:
            raise ValueError("Every report must belong to the requested business.")
        branch_id = _read_identifier(report.get("branch_id"), "branch_id")
        if branch_id in branch_ids_seen:
            raise ValueError("Branch identifiers must be distinct.")
        if _read_definition_context(entry.get("report_definition_context")) != definition:
            raise ValueError("Report-definition contexts must match the requested context.")
        context = _read_reporting_context(report)
        if common_context is not None and context != common_context:
            raise ValueError("Every report must have the same reporting context.")
        summary = _read_summary(report.get("summary"))

        common_context = context
        branch_ids_seen.add(branch_id)
        branches.append({"branch_id": branch_id, **context, "summary": summary})

    branches.sort(key=lambda branch: (isinstance(branch["branch_id"], str), branch["branch_id"]))
    return {
        "business_id": business_id,
        "report_definition_context": definition,
        "branches": branches,
    }
