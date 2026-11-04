"""BE-42 Phase 1: prepare supplied supplier costs for neutral comparison.

This is a provisional internal helper, not a Flask API or database schema.
The caller must authorize and establish the relevance of all supplied records.
Business, product, supplier, and branch identifiers cannot prove authorization
or ownership. Historical purchase costs do not become current supplier quotes.
Branch-transfer assessment required by BW-12 AC4 remains outside this phase.
"""

from decimal import Decimal
import re


def _read_identifier(value, name):
    """Accept positive integers or nonblank strings, without coercion."""
    if type(value) not in (int, str):
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{name} must be a valid identifier.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} must be a valid identifier.")
    return value


def _read_label(value, name):
    """Require explicit text; do not normalize or interpret supplied labels."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string.")
    return value


def _read_cost(value):
    """Preserve unknown costs and exact, finite, nonnegative Decimal amounts."""
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        value = Decimal(value)
    elif not isinstance(value, Decimal):
        raise ValueError("Cost must be None, a Decimal, or a plain decimal string.")
    if not value.is_finite() or value < 0:
        raise ValueError("Cost must be finite and nonnegative.")
    return value


def _read_provenance(value):
    """Copy supplied source annotations; their factual meaning is caller-owned."""
    if not isinstance(value, dict) or not value:
        raise ValueError("Provenance must be a nonempty dictionary of text annotations.")
    if any(
        not isinstance(key, str) or not key.strip()
        or not isinstance(annotation, str) or not annotation.strip()
        for key, annotation in value.items()
    ):
        raise ValueError("Provenance keys and values must be nonblank strings.")
    return value.copy()


def prepare_supplier_cost_comparison(
    observations, *, business_id, product_variant_id, currency, quantity_unit, cost_basis
):
    """Return ordered supplier-cost observations for one explicit comparison scope.

    Provisional input contract:
    - observations is a list/tuple of dictionaries. Each requires business_id,
      product_variant_id, supplier_id, source_id, source_type, cost, currency,
      quantity_unit, cost_basis, and provenance.
    - Identifiers are positive built-in integers or nonblank built-in strings.
      Scope matching preserves types: integer 1 does not match string '1'.
    - source_type is exactly 'historical_purchase' or 'quote'. Optional branch_id
      identifies the source branch and follows the same identifier validation.
      It does not establish branch ownership or transfer eligibility.
    - cost is explicitly None (unknown), a finite nonnegative Decimal, or an
      unsigned ASCII decimal string such as '12' or '12.340'. Strings exclude
      signs, spaces, exponents, and separators. Floats, integers, and booleans
      are rejected. Construct supplied Decimals from exact strings, not floats.
      Decimal amounts, including their precision/exponent, remain unchanged;
      no arithmetic, rounding, quantization, or currency conversion occurs.
    - Currency, quantity unit, and cost basis are explicit nonblank text labels
      supplied both as arguments and in each record. They must match exactly,
      including case, even for unknown costs. Their business meaning is caller-
      owned; this helper does not infer taxes, package sizes, or currency codes.
    - provenance is a nonempty dictionary of nonblank string keys and values,
      for example {'reference': 'synthetic purchase record'}. These are opaque
      annotations, not verified dates or evidence of authorization. No source
      date or other annotation is fabricated. Unknown top-level keys are ignored.

    Provisional output contract:
    Return the explicit scope/context, observations as fresh dictionaries with
    fresh provenance dictionaries and Decimal/None costs, and
    branch_transfer_assessed=False. Preserve input order and every observation,
    including multiple records for the same supplier/source. Empty input gives
    an empty list, not proof that suppliers or transfer alternatives do not exist.
    No rankings, supplier selection, purchase totals, or order actions are made.

    Invalid input or any scope/context mismatch raises ValueError without
    returning partial results or including supplied values in error messages.
    Inputs are unchanged. The caller still owns authentication, authorization,
    source eligibility, and comparability of the actual cost definitions.
    """
    _read_identifier(business_id, "business_id")
    _read_identifier(product_variant_id, "product_variant_id")
    context = {
        "currency": _read_label(currency, "currency"),
        "quantity_unit": _read_label(quantity_unit, "quantity_unit"),
        "cost_basis": _read_label(cost_basis, "cost_basis"),
    }
    if not isinstance(observations, (list, tuple)):
        raise ValueError("Observations must be a list or tuple.")

    prepared = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Each observation must be a dictionary.")
        row = {}
        for name in ("business_id", "product_variant_id", "supplier_id", "source_id"):
            row[name] = _read_identifier(observation.get(name), name)
        for name, selected in (
            ("business_id", business_id), ("product_variant_id", product_variant_id)
        ):
            if type(row[name]) is not type(selected) or row[name] != selected:
                raise ValueError("Each observation must match the selected business and variant.")
        for name, selected in context.items():
            row[name] = _read_label(observation.get(name), name)
            if row[name] != selected:
                raise ValueError("Each observation must match the selected comparison context.")

        source_type = observation.get("source_type")
        if not isinstance(source_type, str) or source_type not in ("historical_purchase", "quote"):
            raise ValueError("Source type must be historical_purchase or quote.")
        if "cost" not in observation:
            raise ValueError("Each observation must include cost; use None when unknown.")
        row["source_type"] = source_type
        row["cost"] = _read_cost(observation["cost"])
        row["provenance"] = _read_provenance(observation.get("provenance"))
        if "branch_id" in observation:
            row["branch_id"] = _read_identifier(observation["branch_id"], "branch_id")
        prepared.append(row)

    return {
        "business_id": business_id,
        "product_variant_id": product_variant_id,
        "currency": currency,
        "quantity_unit": quantity_unit,
        "cost_basis": cost_basis,
        "observations": prepared,
        "branch_transfer_assessed": False,
    }
