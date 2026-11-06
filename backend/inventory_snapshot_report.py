"""BE-48 Phase 1: summarize supplied, already authorized inventory quantities.

PROVISIONAL internal contract, not a final API, report layout, or database schema.
The trusted caller owns authentication, authorization, branch ownership, product
mapping, snapshot/date attribution, and source completeness and non-overlap.
Identifiers and annotations cannot prove any of these properties.

This helper does not retrieve or modify inventory, determine availability or
low/out-of-stock status, value stock, calculate purchases, or interpret returns.
"""

from datetime import date
import re


def _read_identifier(value, name):
    """Preserve positive built-in integers and nonblank built-in strings."""
    if type(value) not in (int, str):
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} must be a valid identifier.")
    return value


def _read_snapshot_date(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("snapshot_date must use YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("snapshot_date must be a valid calendar date.") from None
    return value


def _read_label(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonblank text.")
    return value


def _group_sort_key(group):
    product_id, variant_id, unit, basis = group
    # Sort each identifier type separately so integer 1 stays distinct from '1'.
    return (
        (isinstance(product_id, str), product_id),
        (isinstance(variant_id, str), variant_id),
        unit,
        basis,
    )


def summarize_inventory_snapshot(
    observations, *, business_id, branch_id, snapshot_id, snapshot_date
):
    """Return quantity groups for one explicitly selected scope and snapshot.

    PROVISIONAL input contract:
    - observations is a list/tuple of dictionaries. Each requires business_id,
      branch_id, snapshot_id, snapshot_date, contribution_id, product_id,
      product_variant_id, quantity_unit, quantity_basis, and quantity.
    - Every identifier is a positive built-in int or nonblank built-in str.
      There is no coercion: integer 1 does not match string '1'. Both product
      grouping keys are required; the caller establishes their actual mapping.
    - Every observation must match the requested business, branch, snapshot ID,
      and normalized YYYY-MM-DD snapshot_date. The caller attributes the date;
      this function does not interpret timestamps or reconstruct past stock.
      A mismatch rejects the whole input rather than filtering observations.
    - contribution_id must be distinct across the entire input, including
      different products, variants, units, and bases. Repeated IDs raise
      ValueError, even for identical rows. Distinct IDs do not establish source
      completeness, authenticity, or non-overlap; these remain caller-owned.
    - quantity must be a built-in int. Zero and signed values are summed exactly
      as supplied, without classifying shortages, returns, or adjustments.
      Booleans, floats, numeric strings, and other numeric types are rejected.
      Fractional quantities are outside this technical subset, not prohibited
      by an inferred inventory policy. Python integer arithmetic stays exact.
    - quantity_unit and quantity_basis are explicit nonblank built-in strings.
      Labels are preserved exactly; unlike labels stay in separate groups.
      No conversion, availability derivation, or inventory policy is inferred.
      Extra fields, including status and authorization flags, are ignored.

    PROVISIONAL output contract:
    Return business_id, branch_id, snapshot_id, snapshot_date, and groups.
    Each group has product_id, product_variant_id, quantity_unit, quantity_basis,
    and quantity. Sort product IDs, then variant IDs (integers before strings,
    ascending within each type), then unit and basis text. This is not a ranking.
    Do not combine groups into a total across products, units, or bases.
    An observed zero remains a group. Empty input returns groups=[], which
    means no observations were supplied, not that the branch has zero stock.

    Malformed input raises ValueError without supplied values or partial
    results. Input data stays unchanged; output containers are fresh. Scope
    checks only detect inconsistent input and never grant authorization.
    """
    scope = {
        "business_id": _read_identifier(business_id, "business_id"),
        "branch_id": _read_identifier(branch_id, "branch_id"),
        "snapshot_id": _read_identifier(snapshot_id, "snapshot_id"),
    }
    _read_snapshot_date(snapshot_date)
    if not isinstance(observations, (list, tuple)):
        raise ValueError("Observations must be a list or tuple.")

    contributions_seen = set()
    totals = {}
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("Each observation must be a dictionary.")
        for name, selected in scope.items():
            actual = _read_identifier(row.get(name), name)
            if type(actual) is not type(selected) or actual != selected:
                raise ValueError("Each observation must match the requested scope and snapshot.")
        if _read_snapshot_date(row.get("snapshot_date")) != snapshot_date:
            raise ValueError("Each observation must match the requested snapshot date.")
        contribution_id = _read_identifier(row.get("contribution_id"), "contribution_id")
        if contribution_id in contributions_seen:
            raise ValueError("Contribution identifiers must be distinct within the snapshot.")

        product_id = _read_identifier(row.get("product_id"), "product_id")
        variant_id = _read_identifier(row.get("product_variant_id"), "product_variant_id")
        unit = _read_label(row.get("quantity_unit"), "quantity_unit")
        basis = _read_label(row.get("quantity_basis"), "quantity_basis")
        quantity = row.get("quantity")
        if type(quantity) is not int:
            raise ValueError("Quantity must be a built-in integer.")

        contributions_seen.add(contribution_id)
        group = (product_id, variant_id, unit, basis)
        totals[group] = totals.get(group, 0) + quantity

    groups = []
    for group in sorted(totals, key=_group_sort_key):
        product_id, variant_id, unit, basis = group
        groups.append({
            "product_id": product_id,
            "product_variant_id": variant_id,
            "quantity_unit": unit,
            "quantity_basis": basis,
            "quantity": totals[group],
        })
    return {**scope, "snapshot_date": snapshot_date, "groups": groups}
