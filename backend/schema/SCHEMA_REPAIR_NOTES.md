# BranchWise Schema Repair Draft

This draft starts from the database owner's `BranchWise database.sql`. The
Downloads source was left unchanged. This is a review draft, not a production
migration or an assertion that the schema has been deployed.

## Original problems repaired

- The source had 25 `CREATE TABLE` statements despite describing a 27-table
  design.
- `product_variants` ended after its `attributes_json` check. Its table closure,
  engine options, and business/product foreign keys were missing.
- The source contained two `ALTER TABLE inventory_movements` statements but no
  `inventory_movements` definition. The first appeared before the transfer and
  return source tables were defined.
- No branch-scoped balance table was defined.

## Changes in this draft

- Completed `product_variants` without changing its existing columns, types,
  indexes, unique keys, generated default slot, or checks. Added its
  business-to-`businesses` and composite business/product-to-`products`
  foreign keys, both restrictive, then closed the InnoDB/utf8mb4 table.
  Added the unique `(business_id, product_id, id)` parent key required by the
  existing catalog-change-request composite foreign key.
- Added `branch_inventory` with an unsigned `available_qty`, timestamps, a
  unique `(business_id, branch_id, product_variant_id)` balance key, and
  business-scoped foreign keys to `branches` and `product_variants`.
- Added `inventory_movements` after all of its source tables. Its columns
  represent the existing backend movement types, signed nonzero `quantity_delta`,
  source type/reference, canonical effect reference, actor/system origin,
  optional reason, and source item IDs.
- Added a unique binary-collated effect-reference key for durable movement
  effect deduplication; checks align movement sign, movement/source types,
  origin, and source-record selection with the existing Python rules.
- Moved the source foreign keys from the two premature `ALTER TABLE`
  statements into the new movement table definition. The draft keeps the
  original source links for purchase receipts, sales, sale cancellations,
  transfers, and returns, and keeps `transfer_discrepancy_id` reserved and
  constrained to `NULL`.
- Retained the original branch-creator `ALTER TABLE` after `users` is created;
  it is already in valid dependency order.
- Added restrictive composite business/branch/variant references from
  inventory balances and movements. The referenced parent tables provide the
  required unique keys.
- Preserved the original stock-transfer, purchase-order, receiving, and
  supplier definitions.

The resulting table count is 27: the source's 25 definitions plus
`branch_inventory` and `inventory_movements`.

## Explicit assumptions and limits

- `branch_inventory.available_qty` is the authoritative sellable quantity,
  matching `stock_rules.py` and the movement planner. The draft does not add
  separate inspection, damaged, in-transit, or minimum-stock columns because
  the persistence/update policy for those values is not defined by the
  movement contract. The product-overview rules can consume those optional
  values, so a repository must derive them from approved source data or obtain
  a separate owner decision before treating them as persisted balance fields.
- The effect reference is stored as ASCII because the existing builder creates
  it as JSON from enum values, positive IDs, and source references; the
  supported request-key path restricts keys to visible ASCII. An integration
  must preserve that contract and reject overlong references rather than
  truncate them.
- `inventory_movements` has a unique key and restrictive source references,
  but this DDL does not prohibit `UPDATE` or `DELETE`. Append-only behavior
  remains an application/database-privilege responsibility; add a reviewed
  trigger or least-privilege policy if database-level immutability is required.
- Movement source item IDs are nullable columns constrained to match the
  source type. The persistence adapter must populate the corresponding item
  ID while retaining the exact Python-generated `source_reference` and
  `effect_reference`.
- The composite source-item foreign keys enforce business scope, but do not
  uniformly enforce that a referenced source line has the same variant as the
  movement. The persistence adapter must validate that match; changing the
  existing source-line keys to enforce it in SQL needs owner review.
- The existing `effect_reference` key protects movement-effect replay. It is
  not a general durable request-result/idempotency table; no additional table
  was added.
- The movement-to-balance foreign key requires the scoped `branch_inventory`
  row to exist before a movement is appended. The repository must create a
  missing zero balance in the same transaction before locking and applying
  that movement.
- The existing transfer tables store item aggregates and an optional header
  event-history JSON value, but no separate receipt-event table exists in this
  27-table design. The existing receipt ID and event/idempotency lifecycle must
  be durably associated using an owner-approved representation; this draft
  does not prescribe one.
- Backend pure rules accept Python integers without a database-sized upper
  bound. This draft uses the existing SQL convention of unsigned `INT` for
  available balances and existing item quantities, and signed `BIGINT` for
  movement deltas. Persistence must reject overflow before attempting writes.
- Existing backend DTOs and this SQL are not fully name/type aligned. In
  particular, the SQL uses `variant_code`, `variant_name`,
  `override_cost_price`, and `override_selling_price`, while variant rules use
  `code`, `size`, `color`, `cost_price`, and `selling_price`. The source SQL
  also uses two-decimal product prices while product rules allow three
  decimals. This draft intentionally does not rewrite those existing
  contracts.
- The existing transfer header has no `APPROVED` status, while BE-35 validates
  an approve/reject action and the existing transfer transition rules dispatch
  from `PENDING`. This draft preserves the existing transfer definition and
  does not decide whether approval is separate from dispatch.

## Validation performed

Static checks passed: 27 complete `CREATE TABLE` statements; 94 foreign keys;
no duplicate table, constraint, or per-table index names; no undefined table
references; all referenced parent keys, column types, and dependency ordering
validated; required inventory and movement idempotency keys present. The sole
remaining `ALTER TABLE` targets `branches` after its `users` dependency exists.
Whitespace checks and `git diff --check` passed. A successful static check does
not prove that MySQL accepts or deploys the DDL.

No shared, development, or production database was modified. Live MySQL 8
execution was unavailable because neither a MySQL client nor Docker is
installed. Live execution and foreign-key verification must be performed on a
fresh disposable MySQL 8 database before approval.

No backend services, models, routes, or existing tests were changed as part of
this schema draft. The complete backend regression suite passed
(`4268 passed in 56.33s`). Backend persistence-model compatibility remains to
be reviewed before implementation.
