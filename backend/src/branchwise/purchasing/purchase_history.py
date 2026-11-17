"""BE-31 Phase 1: prepare supplied, already-authorized purchase-order history.

PROVISIONAL internal utility, not a final API or approved database schema.
The trusted caller must verify identity, business/branch access, record
eligibility and completeness, and supply purchase-order history observations.
Purchase-order/receiving requests are not persisted history. This helper does
not retrieve records, load suppliers, or combine orders with receiving events.
"""

from copy import deepcopy


def _validate_identifier(value, name):
    """Require a positive built-in integer without coercion or booleans."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive built-in integer.")


def prepare_purchase_history(
    records, *, business_id, branch_id, supplier_id=None
):
    """Return copied purchase-order observations, optionally for one supplier.

    PROVISIONAL input contract:
    - records is a list/tuple of dictionaries. Each requires business_id,
      branch_id, supplier_id, purchase_order_id, and purchase_order_reference.
      These names and type constraints are internal implementation assumptions,
      not approved database columns or API fields.
    - All identifiers are positive built-in integers. supplier_id=None means
      no supplier filter; an explicit filter follows the same integer rule.
    - Every record must match the selected business and branch. Validate all
      records before filtering, including records for other suppliers.
    - purchase_order_reference is a nonblank string, preserved without trimming.
      Repeated IDs, references and observations are retained; no database
      uniqueness, relationship validity or source completeness is established.
    - Additional caller-supplied data is copied without interpretation. Status,
      date and permission annotations are opaque and never grant authorization.
      The caller still decides which fields may be exposed by a future API.

    Return a fresh list with independently deep-copied records in supplied order.
    Nested containers stay independent of inputs and other returned records.
    Decimal values retain their exact representation without arithmetic,
    rounding, quantization or float conversion. Empty input or no supplier
    matches returns [], without invented rows or supplier details.

    Invalid structure or mixed scope raises ValueError without supplied values
    or partial results. Inputs stay unchanged. There is no I/O, date filtering,
    sorting, deduplication, aggregation, totals calculation, status policy,
    supplier-profile audit history, inventory update or receipt persistence.
    """
    _validate_identifier(business_id, "business_id")
    _validate_identifier(branch_id, "branch_id")
    if supplier_id is not None:
        _validate_identifier(supplier_id, "supplier_id")

    if not isinstance(records, (list, tuple)):
        raise ValueError("Records must be a list or tuple.")

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each purchase history record must be a dictionary.")
        for name in ("business_id", "branch_id", "supplier_id", "purchase_order_id"):
            _validate_identifier(record.get(name), name)
        if record["business_id"] != business_id or record["branch_id"] != branch_id:
            raise ValueError("Every record must match the selected business and branch.")

        reference = record.get("purchase_order_reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("A nonblank purchase_order_reference is required.")

    prepared = []
    for record in records:
        if supplier_id is None or record["supplier_id"] == supplier_id:
            prepared.append(deepcopy(record))
    return prepared
