"""BE-46 Phase 1: stock-alert summary from already classified, authorized data.

Provisional internal input contract (NOT a Flask/API/database contract):
    prepare_dashboard_stock_alerts(items, business_id, branch_id)

Each item is a dictionary with:
    business_id, branch_id, item_id, stock_status

stock_status is one of "low", "out", or "ok". These labels are provisional;
BE-20 must determine the actual status using approved inventory rules.
Every supplied item must match the selected business and branch. A trusted
caller must establish authentication, authorization, and data completeness.
This function cannot establish any of them. It does not compute stock levels,
modify inventory, fetch data, or invent missing items.
"""


def _valid_identifier(value):
    """Allow positive integer IDs or nonblank string IDs, but never booleans."""
    if type(value) is int:
        return value > 0
    if type(value) is str:
        return bool(value.strip())
    return False


def prepare_dashboard_stock_alerts(items, business_id, branch_id):
    """Group already-classified items into low/out alerts, preserving order.

    Raises ValueError for malformed or mixed-scope data. Returns fresh lists
    and dictionaries; inputs are left unchanged. An empty input means there
    were no supplied items, NOT that the business has no stock alerts.
    """
    if not _valid_identifier(business_id) or not _valid_identifier(branch_id):
        raise ValueError("Valid business and branch identifiers are required.")
    if not isinstance(items, (list, tuple)):
        raise ValueError("Items must be a list or tuple.")

    low_items = []
    out_items = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each item must be a dictionary.")
        if not _valid_identifier(item.get("business_id")) or not _valid_identifier(item.get("branch_id")):
            raise ValueError("Invalid item scope.")
        if (type(item["business_id"]) is not type(business_id)
                or item["business_id"] != business_id
                or type(item["branch_id"]) is not type(branch_id)
                or item["branch_id"] != branch_id):
            raise ValueError("An item is outside the selected scope.")
        item_id = item.get("item_id")
        if not _valid_identifier(item_id):
            raise ValueError("Invalid item identifier.")
        status = item.get("stock_status")
        if status not in ("low", "out", "ok") or type(status) is not str:
            raise ValueError("Invalid stock status.")
        if status == "low":
            low_items.append({"item_id": item_id})
        elif status == "out":
            out_items.append({"item_id": item_id})

    return {
        "business_id": business_id,
        "branch_id": branch_id,
        "low_stock_count": len(low_items),
        "out_of_stock_count": len(out_items),
        "low_stock_items": low_items,
        "out_of_stock_items": out_items,
    }
