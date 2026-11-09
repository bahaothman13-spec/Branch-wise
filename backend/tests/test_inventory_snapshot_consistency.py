"""BE-53 regression checks across BE-48 snapshot contribution groups."""

from copy import deepcopy

from inventory_snapshot_report import summarize_inventory_snapshot


def observation(contribution_id, **changes):
    row = {
        "business_id": 1,
        "branch_id": 2,
        "snapshot_id": "snapshot-one",
        "snapshot_date": "2026-11-01",
        "contribution_id": contribution_id,
        "product_id": 10,
        "product_variant_id": 20,
        "quantity_unit": "each",
        "quantity_basis": "on-hand",
        "quantity": 2,
    }
    row.update(changes)
    return row


def test_group_counts_and_quantities_reconcile_to_each_snapshot_contribution():
    rows = [
        observation("c1", quantity=2),
        observation("c2", quantity=3),
        observation("c3", product_id=11, quantity=7),
        observation("c4", product_variant_id=21, quantity=11),
        observation("c5", quantity_unit="box", quantity=13),
        observation("c6", quantity_basis="allocated", quantity=17),
    ]
    original = deepcopy(rows)

    report = summarize_inventory_snapshot(
        rows,
        business_id=1,
        branch_id=2,
        snapshot_id="snapshot-one",
        snapshot_date="2026-11-01",
    )

    source_groups = {}
    for row in rows:
        key = (
            row["product_id"],
            row["product_variant_id"],
            row["quantity_unit"],
            row["quantity_basis"],
        )
        totals = source_groups.setdefault(key, {"quantity": 0, "count": 0})
        totals["quantity"] += row["quantity"]
        totals["count"] += 1

    report_groups = {
        (
            group["product_id"],
            group["product_variant_id"],
            group["quantity_unit"],
            group["quantity_basis"],
        ): group
        for group in report["groups"]
    }

    assert set(report_groups) == set(source_groups)
    assert sum(group["contribution_count"] for group in report["groups"]) == len(rows)
    for key, source in source_groups.items():
        assert report_groups[key]["quantity"] == source["quantity"]
        assert report_groups[key]["contribution_count"] == source["count"]

    assert rows == original
