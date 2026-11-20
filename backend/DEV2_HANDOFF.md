# BE-59: Merged backend handoff

Latest BE-31 internal order-detail status: see the final
"BE-31 - Secure internal Purchase Order Detail Service" section. This single-order
slice does not complete or authorize a Purchase History collection API.

Latest BE-30 database/read-repository status and verification: see the final
"BE-30 - Approved receipt mappings and read-only repository foundation" section.
It supersedes historical BE-30 mapping/repository blockers below.

Verified snapshot: 2026-11-17, worktree `BranchWise_INTEGRATION`, branch `integration/dev1-dev2`, HEAD `d399b7e` (BE-58 startup fix), following merge `92f53cd`. This section supersedes the historical handoff below; historical branch names, paths, counts and statuses describe earlier snapshots only.

## Completion boundaries

- Completed independent implementation: Developer 2 validators, calculations, synthetic pipelines, provisional route boundaries and collection artifacts exist and are tested.
- Completed Git integration: both developers' modules and tests are merged. BE-58 removed the unused empty `src/db.py`; `backend/db.py` is the sole SQLAlchemy/Migrate configuration.
- Partial functional integration: Developer 1's application and registered APIs run; Developer 2 domain helpers are available but their business APIs are not connected to the application.
- Blocked database or policy work: approved schemas, persistent adapters, durable transactions and task-specific policies remain prerequisites below.
- Production readiness: not established. Passing tests demonstrate current contracts and deliberate missing-adapter behavior, not live persistence, safe concurrent writes or complete workflows.

## Layout, startup and verification

`backend/src/app.py` owns the Flask factory and module-level app. Developer 1 infrastructure remains in `backend/`: `db.py`, `session_auth.py`, `auth_workflows.py`, `permissions.py`, `isolation_rules.py`, `repository_contracts.py` and `routes/`. Developer 2 packages live in `backend/src/branchwise/{suppliers,purchasing,transfers,ai,forecasting,replenishment,dashboard,reports}/`. Tests remain in `backend/tests/`; runnable synthetic demonstrations remain in `backend/examples/`.

From the integration repository root in PowerShell:

```powershell
cd backend
..\..\BranchWise_INTEGRATION_VENV\Scripts\python.exe -m flask --app src.app run
```

No PYTHONPATH, pytest configuration or IDE setting is required. For route inspection replace `run` with `routes`. Importing `src.app` constructs the app and loads `backend/.env`; use valid environment settings. Without database settings, development uses memory-only SQLite; production rejects that fallback. No schema or persistent adapter is created by startup.

From the integration repository root, run the complete suite with the isolated environment and repository configuration:

```powershell
..\BranchWise_INTEGRATION_VENV\Scripts\python.exe -m pytest -c backend/pytest.ini backend -q --basetemp backend/.be59-tmp
```

The explicit temporary directory accommodates the known Windows permission issue. The post-BE-58 verification baseline is **3,383 passing tests**, including three fresh-process startup regressions. This is a combined suite result, not a sum of historical developer counts.

## Existing Developer 1 foundations

The app registers eight blueprints: `health`, `auth`, `account_settings`, `branches`, `users`, `products`, `category_variants`, `operations` (49 route rules including Flask static). Coverage includes authentication, account/business settings, branch/staff management, products/categories/variants, overview, adjustments, product requests, sales, cancellation requests and returns. See [API_CONTRACT.md](../API_CONTRACT.md) for individual routes.

`session_auth.py` provides trusted session loading, access-version invalidation, cookie handling and CSRF enforcement. `auth_workflows.py` defines authentication repository interfaces. `permissions.py` and `isolation_rules.py` define role/resource and business/branch scope checks. These are now local merged interfaces; they have not been applied to Developer 2 provisional routes. Preserve server-derived identity and CSRF checks; consult `SESSION_AUTH_FOUNDATION.md`, `API_SECURITY_FOUNDATION.md` and `ROLE_PERMISSIONS.md` before assigning grants.

`repository_contracts.py` defines BranchRepository, UserRepository, BusinessSettingsRepository, ProductRepository, CategoryVariantRepository and OperationsRepository with explicit installers and typed errors. `create_app()` installs no application repositories or persistent auth/session adapters. Missing dependencies deliberately produce errors such as 501 after applicable security/validation checks. Existing operations ports do not establish purchase-history, forecast or supplier contracts.

`db.py` initializes shared SQLAlchemy/Migrate extensions. Transaction hashing, idempotency decisions, ordered balance keys and inventory movement rules exist in Developer 1 modules; they do not supply durable replay storage, locking or atomic MySQL repositories. Approved DDL/migrations and real rollback/concurrency tests remain outstanding. Resolve the database/auth storage decisions documented in `INTEGRATION_READINESS.md`, `BACKEND_DATABASE_DECISIONS.md` and `TRANSACTION_SAFETY_CONTRACT.md` with their owners.

## Developer 2 task status

All 21 assigned main tasks below remain **PARTIAL overall**. The independent slices listed exist; full authorized persistent workflows remain unfinished. BE-32/33 are outside this assignment.

| Task | Implemented/tested slice | Dev2 implementation remaining | Missing dependency / approved team decision |
| --- | --- | --- | --- |
| BE-28 | Supplier validation and provisional route | Authorized CRUD/service and integration tests | Supplier repository/schema; uniqueness/deactivation policy and approved grants |
| BE-29 | PO validation, exact costs, provisional 400/501 route | Scoped supplier/branch/catalog lookups; atomic PO creation/update and response mapping | PO/line contract and adapter; reference uniqueness, duplicate-line and product/variant rules. Creating a PO must not mutate inventory |
| BE-30 | Receiving validation and provisional 400/501 route | Load authorized order/lines; validate receipt against persisted state; commit receipt, balance, movement and replay result atomically | Receipt repository, source identity, locks and durable idempotency; partial/over-receipt, status and cost policies |
| BE-31 | Supplier-filterable in-memory purchase history | Authorized paginated history and supplier records backed by real reads | Purchase-history port, stable record/response identities; date/status/cost/filter definitions; do not infer spending from quotes |
| BE-34 | Structural transfer-request validation; draft-schema SQLAlchemy mappings and internal operation-scoped read adapter | Persistent request creation, authorized service/routes and response mapping | Standalone transfer-history read grant, approved lifecycle/stock decisions, and MySQL verification; reuse Dev1 rules |
| BE-35 | Structural approval/rejection validation; secure internal decision preparation | Persisted decision orchestration and atomic transaction integration | No persisted APPROVED status; approval/dispatch mapping and transactional persistence policies need confirmation |
| BE-36 | Structural receipt validation; secure internal receipt preparation against persisted transfer/items | Persistent receiving, atomic quantity/status updates and inventory effects | Partial-receipt and discrepancy settlement policies; atomic MySQL adapter and integration verification |
| BE-37 | Structural cancellation validation | Authorized cancellation/replay orchestration | Durable source/movement/replay storage; resolve post-dispatch cancellation against Dev1 transition rules |
| BE-38 | Config/client/service/Gemini adapter, mocked tests | Trusted service wiring and operational error handling | Approved provider/model configuration, credentials and live validation; no live success inferred |
| BE-39 | Scoped daily aggregation and missing-day detection | Map trusted sales source to observations with completeness metadata | Sales read contract; cancellation/returns eligibility, timezone, variant mapping and observed-zero policy. Missing days remain missing |
| BE-40 | Moving-average calculation and zero-demand regression fix | Authorized forecasting endpoint/service using BE-39/45 | Trusted sales adapter; approve horizon/window, response and access contracts; algorithm exists already |
| BE-41 | Replenishment calculation and date-aware forecast composition | Trusted same-scope availability read and service/endpoint | Authoritative available quantity, compatible units and snapshot timing; approve horizon/replenishment policy. No automatic PO creation |
| BE-42 | Supplier cost comparison | Scoped supplier/branch alternatives and responses | Real price/availability sources; approve branch ranking, lead-time and cost semantics |
| BE-43 | Question/context preparation | Authorized context retrieval and AI endpoint | Scoped source ports and approved assistant response/data-disclosure policy |
| BE-44 | Decision-input validation | Persist/read authorized decision logs | Decision-log contract/schema, retention and visibility decisions |
| BE-45 | Forecast availability/reliability/fallback helpers | Apply consistent fallback and safe errors through real APIs | Source completeness and approved reliability/response policy |
| BE-46 | Stock-alert summaries | Authorized dashboard aggregation | Trusted stock sources; approved metrics and low-stock definitions |
| BE-47 | Sales/product aggregates and consistency tests | Scoped real-source report endpoint | Sales adapter; approve net sales, returns, date/time and filter semantics |
| BE-48 | Inventory snapshot grouping and consistency tests | Real snapshot integration and deferred purchase-report slice | Snapshot/purchase ports; approve quantity basis, cutoff and purchase metric definitions |
| BE-49 | Same-business branch summaries | Authorized cross-branch report | Complete comparable branch sales source and approved comparison periods/metrics |
| BE-50 | In-memory CSV with exact Decimal support | Authorized filters, source retrieval and export endpoint | Approved report/filter/response contracts and export limits; retain CSV safety |

## Shared tasks and remaining implementation order

| Task | Current status | Remaining work |
| --- | --- | --- |
| BE-51 | PARTIAL: merged auth/CSRF/permission foundations available; Dev2 integration pending | Apply trusted sessions, approved grants and scope checks to each approved Dev2 API |
| BE-52 | PARTIAL: Dev1 transaction/idempotency rules and operations ports exist | Agree purchasing/transfer contracts; implement durable atomic adapters, locks and replay; test real concurrency |
| BE-53 | Completed coverage for current independent slices; ongoing integration coverage | Extend meaningful tests as approved services and adapters are implemented |
| BE-54 | PARTIAL: Dev1 application tests and isolated Dev2 coexistence tests exist | Add authorized Dev2 main-app, persistence, rollback and concurrency integration tests |
| BE-57 | Completed independent collection artifact and contract tests; partial live workflow coverage | Reconcile Dev2 examples with merged auth/cookie/CSRF contracts; enable requests only after corresponding APIs work |
| BE-58 | Completed confirmed startup collision correction in `d399b7e`, with fresh-process regressions; earlier zero-demand fix retained | Address future confirmed defects as integration proceeds; no claim of exhaustive bug elimination |
| BE-59 | Completed post-merge documentation update for this snapshot | Refresh handoff as contracts, adapters and registered APIs change |

The three provisional Developer 2 blueprints are `suppliers`, `purchase_orders` and `purchase_receiving`. In isolated apps they validate requests (400 for invalid input, 501 for structurally valid unimplemented operations). None is registered in the merged app. Purchase history, transfers, AI, forecasting, replenishment, dashboard, reports and export APIs are also not registered. Developer 1's existing operational APIs do not complete these Developer 2 workflows.

Recommended dependency order:

1. Agree schema/version, persistent auth/session/rate-limit ownership, supplier/purchasing read/write ports and approved grants. Resolve transfer lifecycle/discrepancy/cancellation and reporting definitions with the relevant owners. Git/layout integration and startup repair are already complete.
2. BE-51 and BE-28: wire trusted security and approved supplier repositories/services, then test denied and cross-tenant requests before enabling the supplier API.
3. BE-29, BE-52 and BE-30: persist orders without inventory mutation; implement durable atomic receiving, ledger/balance changes and idempotent replay. Prove rollback, payload conflicts and concurrent duplicate handling on the approved database.
4. BE-31 and deferred BE-48 purchasing inputs: implement scoped persisted history and supplier records.
5. BE-34?37: implement transfer services only after lifecycle rules and durable movement/replay adapters are approved; reuse Developer 1 rules.
6. BE-39, BE-40/45, BE-41, BE-42: connect trusted sales completeness, forecast/fallback, authoritative stock and supplier/branch alternatives in that order. Preserve missing-versus-zero, unit and tenant boundaries.
7. BE-46?50 and BE-38/43/44: connect approved reporting/snapshot sources, safe exports, provider configuration, scoped AI context and persistent decision logs. No live Gemini validation is inferred from mocked tests.
8. Carry BE-53/54 security and integration tests through every step; update BE-57 collection requests when APIs become available, rerun the combined suite, and refresh BE-59. Production review additionally requires persistent adapters, operational configuration and real database/concurrency evidence.

---

# Historical handoff (preserved)

# BE-59: Backend Developer 2 — implementation and integration handoff

**Project:** BranchWise — Smart Inventory Management & Decision Support System
**Working repository:** OUTER `Branch-wise` repository, branch `backend-dev2`
**Documentation snapshot:** 2026-11-16 (BE-57 documentation update; test results reported by the user)
**Last confirmed pushed commit:** `53d711f` (`backend-dev2`; latest pushed commit reported by the user)
**Latest reported full backend suite:** **2,242 passed**
**Worktree at verification:** `backend-dev2` is aligned with `origin/backend-dev2`; an unrelated untracked `.vscode/` directory is present and was not changed.

> **Completion boundary:** Developer 2 has implemented and tested **independent, provisional Python components for all 21 assigned main tasks**. Those **21 tasks remain PARTIAL**; most do not yet have connected Flask routes, trusted authorization, approved MySQL record contracts, or end-to-end integration. BE-34, BE-35, BE-36, and BE-37 include structural validation only; transfer integration remains blocked pending approved contracts and policies. A passing unit suite is **not** proof of deployed APIs, real data access, or safe database transactions.
>
> This handoff describes work reported and tested during development; it is not a new approval of any provisional API/schema/business rule. Verify repository content and rerun tests after integration or code changes.

## 1. Main task inventory

The file paths below are relative to this `backend/` directory. `Phase 1` means the initial independent component, **not** full task completion.

| Task | Work completed / phases | Production module | Test module | Status |
| --- | --- | --- | --- | --- |
| **BE-28** Supplier Management APIs | Phase 1: supplier input validation | [`supplier_validation.py`](src/branchwise/suppliers/supplier_validation.py) | [`test_supplier_validation.py`](tests/test_supplier_validation.py) | PARTIAL |
| **BE-29** Purchase Order APIs | Phase 1: purchase-order input validation; Phase 2: provisional Purchase Order API | [`purchase_order_validation.py`](src/branchwise/purchasing/purchase_order_validation.py), [`purchase_order_routes.py`](src/branchwise/purchasing/purchase_order_routes.py) | [`test_purchase_order_validation.py`](tests/test_purchase_order_validation.py), [`test_purchase_order_routes.py`](tests/test_purchase_order_routes.py) | PARTIAL |
| **BE-30** Purchase Receiving | Phase 1: receiving-input validation; Phase 2: provisional Purchase Receiving API | [`purchase_receiving_validation.py`](src/branchwise/purchasing/purchase_receiving_validation.py), [`purchase_receiving_routes.py`](src/branchwise/purchasing/purchase_receiving_routes.py) | [`test_purchase_receiving_validation.py`](tests/test_purchase_receiving_validation.py), [`test_purchase_receiving_routes.py`](tests/test_purchase_receiving_routes.py) | PARTIAL |
| **BE-31** Purchase History & Supplier Records APIs | Phase 1: supplier-filterable purchase-history preparation | [`purchase_history.py`](src/branchwise/purchasing/purchase_history.py) | [`test_purchase_history.py`](tests/test_purchase_history.py) | PARTIAL |
| **BE-34** Stock Transfer Request APIs | Phase 1: structural request validation | [`transfer_request_validation.py`](src/branchwise/transfers/transfer_request_validation.py) | [`test_transfer_request_validation.py`](tests/test_transfer_request_validation.py) | PARTIAL |
| **BE-35** Transfer Approval & Dispatch Processing | Phase 1: approval/rejection structural validation; dispatch integration blocked | [`transfer_decision_validation.py`](src/branchwise/transfers/transfer_decision_validation.py) | [`test_transfer_decision_validation.py`](tests/test_transfer_decision_validation.py) | PARTIAL |
| **BE-36** Transfer Receiving & Discrepancy Handling | Phase 1: receiving-input structural validation | [`transfer_receiving_validation.py`](src/branchwise/transfers/transfer_receiving_validation.py) | [`test_transfer_receiving_validation.py`](tests/test_transfer_receiving_validation.py) | PARTIAL |
| **BE-37** Transfer Cancellation & Idempotency Rules | Phase 1: structural cancellation-input validation | [`transfer_cancellation_validation.py`](src/branchwise/transfers/transfer_cancellation_validation.py) | [`test_transfer_cancellation_validation.py`](tests/test_transfer_cancellation_validation.py) | PARTIAL |
| **BE-38** AI Service Integration Layer | Phases 1–3: configuration, generic service/client boundary, Gemini adapter and mocked tests | [`ai_config.py`](src/branchwise/ai/ai_config.py), [`ai_client.py`](src/branchwise/ai/ai_client.py), [`ai_service.py`](src/branchwise/ai/ai_service.py), [`gemini_provider.py`](src/branchwise/ai/gemini_provider.py) | `tests/test_ai_*.py`, [`test_gemini_provider.py`](tests/test_gemini_provider.py) | PARTIAL |
| **BE-39** Historical Sales Data Preparation | Phase 1: daily sales-data preparation/aggregation | [`sales_history.py`](src/branchwise/forecasting/sales_history.py) | [`test_sales_history.py`](tests/test_sales_history.py) | PARTIAL |
| **BE-40** Demand Forecasting API | Phase 1: independent moving-average forecasting calculations | [`demand_forecast.py`](src/branchwise/forecasting/demand_forecast.py) | [`test_demand_forecast.py`](tests/test_demand_forecast.py) | PARTIAL |
| **BE-41** Replenishment Recommendation Service | Phase 1: suggested replenishment quantity calculation | [`replenishment.py`](src/branchwise/replenishment/replenishment.py) | [`test_replenishment.py`](tests/test_replenishment.py) | PARTIAL |
| **BE-42** Supplier & Branch Alternative Comparison | Phase 1: supplier cost-comparison calculations/validation | [`alternative_comparison.py`](src/branchwise/replenishment/alternative_comparison.py) | [`test_alternative_comparison.py`](tests/test_alternative_comparison.py) | PARTIAL |
| **BE-43** AI Business Question Assistant API | Phase 1: business question and context preparation | [`business_question.py`](src/branchwise/ai/business_question.py) | [`test_business_question.py`](tests/test_business_question.py) | PARTIAL |
| **BE-44** AI Decision Log APIs | Phase 1: AI decision-input validation | [`decision_input.py`](src/branchwise/ai/decision_input.py) | [`test_decision_input.py`](tests/test_decision_input.py) | PARTIAL |
| **BE-45** AI Reliability, Validation & Fallback Handling | Phase 1: forecast availability/reliability handling | [`forecast_reliability.py`](src/branchwise/forecasting/forecast_reliability.py) | [`test_forecast_reliability.py`](tests/test_forecast_reliability.py) | PARTIAL |
| **BE-46** Dashboard Summary APIs | Phase 1: low/out-of-stock alert summaries | [`dashboard_stock_alerts.py`](src/branchwise/dashboard/dashboard_stock_alerts.py) | [`test_dashboard_stock_alerts.py`](tests/test_dashboard_stock_alerts.py) | PARTIAL |
| **BE-47** Sales & Product Performance Reports | Phases 1–3: sales/product calculations, observed-day and date/product breakdowns | [`sales_performance_report.py`](src/branchwise/reports/sales_performance_report.py) | [`test_sales_performance_report.py`](tests/test_sales_performance_report.py) | PARTIAL |
| **BE-48** Inventory & Purchase Reports | Phase 1: inventory snapshot quantity summary; Phase 2: per-group contribution counts; purchase-report slice deferred | [`inventory_snapshot_report.py`](src/branchwise/reports/inventory_snapshot_report.py) | [`test_inventory_snapshot_report.py`](tests/test_inventory_snapshot_report.py) | PARTIAL |
| **BE-49** Business & Branch Comparison Reports | Phase 1: same-business branch sales-summary preparation | [`branch_comparison_report.py`](src/branchwise/reports/branch_comparison_report.py) | [`test_branch_comparison_report.py`](tests/test_branch_comparison_report.py) | PARTIAL |
| **BE-50** Report Filtering & Export APIs | Phase 1: safe in-memory CSV; **Phase 2: exact `Decimal` CSV support** | [`report_export.py`](src/branchwise/reports/report_export.py) | [`test_report_export.py`](tests/test_report_export.py) | PARTIAL |

**Shared work status:** See the status table below. Implemented unit tests and isolated provisional-route checks do not constitute production authentication, persistence, transaction safety, or deployment readiness.

| Task | Status | Implemented/tested work | Still required / dependency |
| --- | --- | --- | --- |
| **BE-51** Secure API routes using approved authentication | BLOCKED — not integrated | Provisional routes validate request structure; they reject client-supplied authorization metadata and remain unregistered in the main app. | Developer 1 must provide the approved trusted authentication/authorization interface. Then integrate and test access-control behavior; do not invent identity or permissions. |
| **BE-52** Transaction and idempotency integration contracts | BLOCKED — contracts not established | Existing helpers and route tests do not perform persistence or inventory mutations. | Agree repository/transaction ownership and durable idempotency semantics with Developer 1/database owner and obtain approved business rules before implementing. |
| **BE-53** Unit tests for implementations | IMPLEMENTED for current provisional work; ongoing | The latest reported backend suite passes **2,242 tests**. Tests cover pure helpers, synthetic-data pipeline/report consistency, and provisional-route boundaries. | Add focused tests as approved integrations are implemented. Existing tests do not prove live data, authentication, authorization, concurrency, or transaction correctness. |
| **BE-54** API integration tests | PARTIAL — provisional only | Seven tests in [`test_provisional_routes_integration.py`](tests/test_provisional_routes_integration.py) verify Blueprint coexistence, expected HTTP 501 responses, main-app non-registration, and no known database/network access. | Real API/repository integration tests require approved APIs and Developer 1's auth/database interfaces; later cover authorization, persistence, transactions, retries, and real data flow. |
| **BE-57** Postman Collection | IMPLEMENTED - independent collection artifact | Valid, importable v2.1 collection with runnable Health, guarded provisional examples, documentation and 9 targeted tests; commit `53d711f`. | Update after approved API/authentication/database integration. Provisional requests do not establish live integration or production readiness. |
| **BE-58** Review and fix backend bugs in owned files | ONGOING | The current suite passes; this handoff review did not identify a specific owned-code defect to fix. | Continue review and targeted regression testing as changes are made. Passing tests do not prove absence of bugs. |
| **BE-59** Backend documentation and handoff | IN PROGRESS | This handoff records provisional implementation, test history, and integration/security boundaries. | Keep status and metadata current; do not describe provisional helpers or tests as production-complete. |

These statuses do not change the completion boundary: all 21 implemented-core-task slices, including BE-34, BE-35, BE-36, and BE-37, remain **PARTIAL**. No production readiness is claimed.

## 2. Suppliers, purchase orders, receiving and history (BE-28–BE-31)

### BE-28 — Supplier Management, Phase 1

`validate_supplier_input(data)` returns a field-error dictionary; `{}` means validation passed **only**, not that a supplier was saved.

- Required `name`: nonblank string. Optional `contact_person`, `phone`, `email`, `address`, `notes` accept omitted/`None`/string values. Populated email gets a basic syntax check; phone is type-only.
- Does not modify inputs. Unknown keys are **not** a whitelist for persistence; field names are provisional.
- **Recorded tests:** 51 for the initial component.
- **Remaining:** supplier CRUD, business ownership/RBAC, database design/persistence, duplicates, limits and deactivate policy; coordinated with BE-11 and the database team.

### BE-29 — Purchase Order APIs, Phase 1

`validate_purchase_order_input(data)` checks a provisional dictionary containing positive built-in integer `supplier_id`, `branch_id`, and a nonempty `items` list. Each item requires a product or variant reference, a positive integer `quantity`, and a finite nonnegative purchase cost. Costs may be supported exact values (including `Decimal` or decimal strings) but **not floats**. The implementation deliberately uses `decimal.localcontext()` so an invalid value does not leave Decimal flags in unrelated code.

- Product/variant relationship, duplicate lines, supplier ownership, real cost basis, unique order reference, persisted status, and request envelope remain undecided.
- **Creating a PO must not change inventory.** This validator does not create orders, reserve stock, authenticate or persist.
- **Recorded tests:** 41 (after Decimal-context regression fix).
- **Remaining:** approved PO API/schema/line contract, creation/updates, uniqueness, authorized business/branch/supplier loading, database transactions and integration tests.

### BE-29 Phase 2 — Provisional Purchase Order API

Added `backend/purchase_order_routes.py` with isolated Flask Blueprint `purchase_order_bp` for `POST /api/purchase-orders`, reusing the existing `validate_purchase_order_input()` helper unchanged.

- Returns HTTP 400 for invalid JSON/body shapes, validator errors, and unknown fields at both order and item levels.
- Preserves existing field-error messages, exact cost validation without normalization/conversion, and product-only, variant-only, and both-reference behavior.
- Valid requests return HTTP 501 because authentication, authorization, and database persistence are not integrated, with the message: `Purchase order creation is pending authentication, authorization, and database integration.`

The Blueprint is **not registered** in the main Flask application (`backend/app.py`). It performs no database access, ID generation, order creation, or inventory changes. Caller-supplied IDs and metadata do not authorize access; real integration still requires Developer 1's trusted authentication/RBAC, business/branch/resource ownership checks, approved schema/repository contracts, and integration tests.

Added `backend/tests/test_purchase_order_routes.py` containing **84 passing isolated Flask API tests**, including raw nonfinite/overflowed JSON cost literals and exact validator-message assertions. No MySQL, external services, or real authentication are required by these tests.

**Commit:** `c6e7764` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,650 passed, 0 failed**.

**BE-29 remains PARTIAL.**

### BE-29 Phase 3 — Purchase Order API Integration Readiness

**Already implemented and tested:** The provisional `POST /api/purchase-orders` Blueprint accepts a JSON object with root fields `supplier_id`, `branch_id`, and nonempty `items`; each item permits `product_id`, `variant_id`, `quantity`, and `purchase_cost`. It rejects unknown root/item fields, validates positive built-in integer IDs and quantities, and accepts a product reference, variant reference, or both. Costs are checked as finite and nonnegative without float support. Invalid requests return HTTP 400; valid structural requests return HTTP 501 with a pending-integration message. The existing route suite records 84 tests covering malformed bodies, field/type/cost validation, unknown metadata, accepted structural variations, and the 501 response. The route and validator do not create orders or change inventory.

**Verified available dependencies:** The route uses Flask and the provisional validator. `backend/app.py` currently exposes only `/api/health`; it does not register the Purchase Order Blueprint. The outer backend contains no usable authentication/RBAC interface, purchase-order model/schema, repository, or persistence service; `backend/db.py` is empty. The presence of a MySQL connector dependency, if installed, is not a database contract.

**Existing references are not verified relationships:** `supplier_id`, `branch_id`, and item `product_id`/`variant_id` receive structural checks only. The route does not establish that these records exist, belong to the same business, are active, or are mutually compatible. There is no established supplier, product, variant, or business/branch ownership contract to load or authorize against. Client-supplied business/user/role/permission metadata is rejected; the eventual business scope must come from trusted server-side identity and authorization, not the request body.

**Missing or unapproved decisions:** Approve order and line schema, status/lifecycle, generated order reference, currency and cost basis, field limits and normalization, supplier/branch ownership rules, product-versus-variant relationship and whether both may be supplied, duplicate line behavior, supplier eligibility, price/quantity policies, idempotency, and response/error semantics. The provisional validator's field names and accepted values do not settle these database or business rules.

**Blocked integration checklist (do not replace HTTP 501 yet):**

1. **Developer 1 / authentication and database owners:** provide trusted actor/business/branch context and the authorization interface; approve PO, line, supplier, product, and variant schema/relationships, constraints, repository/service contracts, and transaction ownership.
2. **Team/API owners:** approve request/response and error contracts, cost/quantity semantics, reference rules, duplicate/idempotency behavior, and order lifecycle. Resolve required business decisions before changing validation.
3. **Developer 2, after contracts are available:** update the route/validation integration to authorize the trusted business and branch, load and verify supplier/product/variant ownership and eligibility through approved services, enforce the approved allowlist and rules, and create the order through the approved transactional persistence interface. Do not trust client-supplied identifiers as authorization or status.
4. **Developer 1 and Developer 2 together:** test authentication failures, cross-business/branch and invalid-resource references, validation boundaries, transaction rollback, duplicate/retry behavior, response disclosure, and the invariant that purchase-order creation does not update inventory. Register the Blueprint only after integration and deployment ownership explicitly approve it.

Until these contracts and tests exist, real order creation, authorization, supplier/product/variant lookup, persistence, transaction guarantees, and deployment wiring remain blocked. Keep the Blueprint unregistered in `app.py` and BE-29 **PARTIAL**. Authentication and database/schema ownership remain with Developer 1; Developer 2 integration work is contingent on those interfaces and approved business decisions.

### BE-30 — Purchase Receiving, Phase 1

`validate_purchase_receiving_input(data)` checks the provisional structure with `purchase_order_id` and one or more lines with `purchase_order_line_id` and `received_quantity`. IDs are positive built-in integers (not booleans). The provisional quantity accepts a **nonnegative** built-in integer, including zero for **structural validation only**. No policy approves zero-quantity receipt transactions from this check.

- Returns deterministic field errors, does not change input, ignores unknown untrusted metadata, and performs **no receipt, stock, state, ledger, or persistence action**.
- **Recorded tests:** 72.
- **Remaining:** approved receipt and PO-line contract, actual quantities/units, partial/over-receipt rules, trusted actor/timestamp, authorization, authoritative stock service, append-only movements, **atomic** transaction and durable duplicate/retry protection. Caller-supplied IDs cannot authorize actions.

### BE-30 Phase 2 — Provisional Purchase Receiving API

Added [`purchase_receiving_routes.py`](src/branchwise/purchasing/purchase_receiving_routes.py) with an isolated Flask Blueprint for `POST /api/purchase-receiving`, reusing the existing purchase-receiving input validation.

- Valid requests return HTTP 501 because authentication, authorization, business rules, and persistence are not integrated.
- The Blueprint is **not registered** in `backend/app.py`. No real receiving, database writes, or inventory changes occur.
- **Recorded API tests:** 90 passing in [`test_purchase_receiving_routes.py`](tests/test_purchase_receiving_routes.py).
- **Commit:** `4856b80` on `backend-dev2`.
- **Remaining:** authentication, authorization, approved receiving business rules, and persistence/inventory integration.

**BE-30 remains PARTIAL.**

### BE-31 — Purchase History & Supplier Records, Phase 1

`prepare_purchase_history(records, *, business_id, branch_id, supplier_id=None)` takes **already-authorized, already-retrieved** purchase-order history records. It validates **all** records for the requested business/branch before optionally filtering by supplier. The provisional envelope includes business, branch, supplier, PO ID and order reference.

- Preserves input order, repeated observations, source values and `Decimal` representation; returns fresh independent copies. Invalid or mixed-scope observations raise `ValueError`.
- Does **not** invent missing records, calculate spending, combine receiving events, create historical supplier snapshots, or perform date filtering before its semantics are defined.
- **Recorded tests:** 132.
- **Remaining:** trusted database retrieval and source completeness, supplier/order/receipt relations, RBAC, record granularity, dates/timezones, pagination/filtering and Flask API. BE-29/BE-30 input dictionaries are **not** persisted history.

#### BE-29–BE-31 — Runnable Purchasing Components Demo

Added [`examples/purchasing_demo.py`](examples/purchasing_demo.py) to demonstrate the provisional purchase-order and receiving request validation in an isolated Flask test application, plus supplier filtering of caller-supplied synthetic purchase-history records. Run from the repository root:

```powershell
.\.venv\Scripts\python.exe backend\examples\purchasing_demo.py
```

Invalid purchase-order and purchase-receiving requests return HTTP 400; structurally valid requests return HTTP 501 because the operations are not implemented. BE-31 demonstrates filtering supplied history by supplier. The demo uses synthetic data: it persists no orders or receipts, updates no inventory, and does not establish a real relationship between the sample history and the requests.

**Focused purchasing tests:** **426 passed**. **Full backend suite:** **1,802 passed, 0 failed**. **Commit:** `dfacd38` on `backend-dev2`; **GitHub push:** successful. This demo does not change the Developer 1 authentication/database ownership boundary or complete production integration. **BE-29, BE-30, and BE-31 remain PARTIAL.**

## 3. AI and decision support (BE-38–BE-45)

### BE-38 — AI Service Integration Layer, Phases 1–3

**Phase 1/2:** [`ai_config.py`](src/branchwise/ai/ai_config.py), [`ai_client.py`](src/branchwise/ai/ai_client.py), [`ai_service.py`](src/branchwise/ai/ai_service.py) provide configuration, provider-calling and safe text-service boundaries. `run_ai(request_data, provider=None)` accepts a dictionary containing only a nonblank `text` string, checks lengths, invokes an application-supplied callable, and validates a reply containing only `text`. It rejects output that contains the exact configured API key. No SQL, generated code execution or business-data updates are performed by the generic service.

An example with a **fake provider** (requires suitable process configuration):

```python
from ai_service import run_ai

def fake_provider(text, *, api_key, timeout):
    return {"text": "A local example answer."}

result = run_ai({"text": "Hello"}, provider=fake_provider)
# {"ok": True, "data": {"text": "A local example answer."}, "error": None}
```

The provider is trusted application code, never user-supplied executable input. Without a provider, valid configured calls return `AI_NOT_CONFIGURED`.

**Phase 3:** [`gemini_provider.py`](src/branchwise/ai/gemini_provider.py) implements an explicitly selected Google GenAI adapter; SDK-client calls were **mocked in tests**, not verified against live credentials. The existing code/handoff describes `google-genai==2.34.0` and an Interactions-style call using `store=False`, plus timeout/connection/provider-error mapping. It requires an authorized team member to check the live SDK contract, supported model, access and handling before deployment. **The configured model string `gemini-3.8-flash` is not verified here as a valid live/public model; do not assume it will work.**

The generic service returns error codes including `AI_INVALID_INPUT`, `AI_CONFIG_ERROR`, `AI_NOT_CONFIGURED`, `AI_TIMEOUT`, `AI_CONNECTION_ERROR`, `AI_PROVIDER_ERROR`, and `AI_INVALID_OUTPUT`. The errors should remain safe; do not print API keys or provider exception details.

**Remaining:** authorized credentials/live model verification, trusted business context, Flask routes, RBAC, quotas/timeouts/fallback policy, operational/security tests. **BE-38 is PARTIAL.**

### BE-39 — Historical Sales Data Preparation, Phase 1

[`sales_history.py`](src/branchwise/forecasting/sales_history.py) prepares and aggregates caller-supplied daily sales observations. Dates and sales inclusion are trusted-input responsibilities: gaps, missing dates, cancellations and returns are **not** silently turned into invented zero sales. **Recorded new tests:** 78. **Remaining:** approved actual sales sources, attribution, eligibility and date/timezone policies, database adapters, BE-40 integration.

### BE-40 — Demand Forecasting API, Phase 1

[`demand_forecast.py`](src/branchwise/forecasting/demand_forecast.py) performs preliminary **moving-average calculations** on supplied historical data. **Recorded new tests:** 69. **Remaining:** approved forecast period/eligibility, real history/Flask/API, scarce-data policy and validation of forecast usefulness.

### BE-41 — Replenishment Recommendation Service, Phase 1

[`replenishment.py`](src/branchwise/replenishment/replenishment.py) calculates a provisional quantity `max(0, ceil(forecast_demand - available_qty))` from supplied inputs. It **does not** read inventory or create purchases. **Recorded new tests:** 61. **Remaining:** authoritative BE-20 availability, agreed units/policies, supplier alternatives, service/AI/API integration.

#### BE-41 Phase 2 — Forecast-to-Replenishment Composition

Added [`replenishment_service.py`](src/branchwise/replenishment/replenishment_service.py) and [`test_replenishment_service.py`](tests/test_replenishment_service.py), with **16 passing tests**.

The provisional internal service combines BE-45 date-aware forecast availability with the existing BE-41 replenishment calculation. An unavailable forecast, including missing date coverage or failed reliability checks, returns no replenishment recommendation. A valid zero forecast remains available and is passed to the existing calculation; it is not treated as an unavailable forecast.

No inventory changes, purchase orders, or database operations occur. The service does not establish that supplied stock is authoritative. Authoritative available stock, matching item/branch identity, compatible quantity units, and an appropriate forecast horizon remain caller/integration responsibilities.

**Commit:** `7301d6b` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,778 passed, 0 failed**.

**BE-41 remains PARTIAL.**

#### BE-39–BE-41 — Runnable Forecast-to-Replenishment Demo

Added [`examples/forecast_replenishment_demo.py`](examples/forecast_replenishment_demo.py), a runnable synthetic-data demonstration of the existing historical-sales → forecasting → replenishment pipeline. From the repository root, run:

```powershell
.\.venv\Scripts\python.exe backend\examples\forecast_replenishment_demo.py
```

The complete-history scenario produces a daily average of **5.0**, a forecast total of **20.0**, and a replenishment recommendation of **15**. The missing-history scenario identifies **November 2, 2026** as missing and returns no forecast or recommendation. The demo calls the existing pipeline and does not duplicate its calculations.

The demo uses synthetic observations and an explicitly supplied example available quantity. It does not provide real database, authentication, or inventory integration; existing ownership and integration boundaries remain unchanged. **Focused BE-39–BE-41 tests:** **258 passed**. **Full backend suite:** **1,802 passed, 0 failed**. **Commit:** `1fd2601` on `backend-dev2`; **GitHub push:** successful.

BE-39, BE-40, and BE-41 remain **PARTIAL** pending approved sales and inventory sources, trusted authorization, and agreed business/API contracts.

### BE-42 — Supplier & Branch Alternative Comparison, Phase 1

[`alternative_comparison.py`](src/branchwise/replenishment/alternative_comparison.py) validates and compares supplied supplier-cost observations using exact `Decimal`, compatible currency/unit/cost basis and explicit unknown-cost handling. The supplier-cost slice is not a real purchase ledger, ranking, or full branch-transfer alternative assessment. **Recorded new tests:** 201. **Remaining:** approved quotes, supplier/branch data, transfer policy, authorization and final selection criteria.

### BE-43 — AI Business Question Assistant API, Phase 1

[`business_question.py`](src/branchwise/ai/business_question.py) prepares business questions and their supplied data context; it does **not** retrieve actual business data or deliver a live AI-assisted report. **Recorded new tests:** 45. **Remaining:** authenticated data retrieval, approved context, AI service/Flask wiring and safe answer behavior.

### BE-44 — AI Decision Log APIs, Phase 1

[`decision_input.py`](src/branchwise/ai/decision_input.py) validates provisional decision input, including `accepted`, `rejected`, `deferred` and an optional reason. It does **not** save a decision. **Recorded new tests:** 48. **Remaining:** trusted user/time, recommendation link/inputs, DB audit history, actual-versus-estimated outcomes and access controls.

### BE-45 — AI Reliability, Validation & Fallback Handling, Phase 1

[`forecast_reliability.py`](src/branchwise/forecasting/forecast_reliability.py) distinguishes an **unavailable forecast** from a legitimate predicted zero and prepares guarded status handling. **Recorded new tests:** 42. **Remaining:** end-to-end availability/low-data/failure fallbacks, service wiring, user-facing messages and operational verification.

#### BE-45 Phase 2 — Date-aware Forecast Availability

Added [`forecast_availability.py`](src/branchwise/forecasting/forecast_availability.py) and [`test_forecast_availability.py`](tests/test_forecast_availability.py), with **22 passing tests**.

The adapter accepts an explicit reporting interval and already-authorized daily observations. It distinguishes an explicitly observed zero-sales day from a missing date, and returns an unavailable result without forecasting when date coverage is incomplete. With complete coverage, it reuses the existing BE-39 daily-history preparation, BE-40 demand forecast, and BE-45 reliability helpers without changing their aggregation or forecasting formulas.

Complete coverage of supplied observations does **not** prove database source completeness or authorization; trusted retrieval, source eligibility, and access checks remain caller responsibilities.

**Commit:** `3c85724` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,762 passed, 0 failed**.

**BE-45 remains PARTIAL.**

## 4. Dashboards, reports and export (BE-46–BE-50)

### BE-46 — Dashboard Summary APIs, Phase 1

[`dashboard_stock_alerts.py`](src/branchwise/dashboard/dashboard_stock_alerts.py) summarizes **already-classified** low-stock/out-of-stock observations within explicit business/branch scope. It does not compute authoritative stock or decide alert thresholds. **Recorded new tests:** 61. **Recorded commit:** `97e9d0f`. **Remaining:** BE-20 stock classification, other dashboard metrics, authorization and API integration.

### BE-47 — Sales & Product Performance Reports, Phase 1

`calculate_sales_performance_report()` in [`sales_performance_report.py`](src/branchwise/reports/sales_performance_report.py) works with caller-supplied eligible sales lines: exact `Decimal` sales sums, distinct transaction counting while repeated source lines still affect totals, product quantity/value grouping, inclusive date filtering and scope validation. It does **not** invent cancellation/return/net-sales rules, rankings or transaction eligibility. **Recorded new tests:** 195. **Recorded commit:** `90d1c5f`. **Remaining:** real data, approved eligibility/money rules, trusted authorization and final API/report contract.

#### BE-47 Phase 2 — Daily Sales Performance Breakdown

`calculate_sales_performance_report()` now also returns `daily_totals`, containing each observed date's distinct transaction count, total quantity, and exact `Decimal` sales total in chronological order. Unobserved dates are not fabricated; explicitly observed zero values are preserved. The existing overall and product summaries remain unchanged.

**Recorded BE-47 tests:** 196 passed. **Full backend suite:** 1,798 passed, 0 failed. **Commit:** `ca0458c` on `backend-dev2`; **GitHub push:** successful.

BE-47 remains **PARTIAL**: real trusted data retrieval, authorization, source completeness, and approved sales eligibility/money rules remain caller/integration dependencies.

#### BE-47 Phase 3 — Per-Product Daily Sales Breakdown

`calculate_sales_performance_report()` now also returns `daily_product_totals`, grouping observed sales by date and typed product ID. Each entry contains the distinct transaction count, total quantity, and exact `Decimal` sales total for that date/product pair. Results are ordered chronologically, then by product ID, with integer IDs before string IDs. Missing date/product combinations are not fabricated; observed zero values are preserved. The existing `summary`, `products`, and `daily_totals` outputs remain unchanged.

**Recorded BE-47 tests:** 197 passed. **Full backend suite:** 1,799 passed, 0 failed. **Commit:** `cfb5510` on `backend-dev2`; **GitHub push:** successful.

BE-47 remains **PARTIAL**: trusted data retrieval, authorization, and source completeness remain upstream dependencies; this helper does not establish them.

### BE-48 — Inventory & Purchase Reports, Phase 1

`summarize_inventory_snapshot()` in [`inventory_snapshot_report.py`](src/branchwise/reports/inventory_snapshot_report.py) aggregates **one explicitly identified** business/branch inventory snapshot by product/variant, unit and quantity basis, using exact signed built-in integers. It rejects mixed snapshots and repeated source contribution IDs, without claiming source completeness/non-overlap. **Do not add quantities across different snapshots.** **Recorded new tests:** 116. **Recorded commit:** `242fd6c`. **Remaining:** BE-20 authoritative inventory, BE-31 purchase report contracts, financial valuation policy if applicable, Flask/export and authorization. Purchase calculations were deferred.

#### BE-48 Phase 2 — Inventory Snapshot Contribution Counts

Each existing inventory snapshot group now includes `contribution_count`, counting accepted supplied observations after the existing duplicate-identity validation and grouping by product, variant, quantity unit, and quantity basis. Existing quantity calculations and deterministic group ordering are unchanged. Quantities remain built-in integers under the current contract; no Decimal quantity behavior was introduced. Counts describe supplied contributions only and do not establish inventory completeness or source authenticity.

**Recorded BE-48 tests:** 117 passed. **Full backend suite:** 1,800 passed, 0 failed. **Commit:** `b8f8bf7` on `backend-dev2`; **GitHub push:** successful.

BE-48 remains **PARTIAL**: trusted inventory retrieval, authorization, and source completeness remain upstream dependencies. Developer 1's ownership of authentication, database, and authorization contracts is unchanged.

### BE-49 — Business & Branch Comparison Reports, Phase 1

`prepare_branch_sales_comparison(branch_reports, *, business_id, report_definition_context)` in [`branch_comparison_report.py`](src/branchwise/reports/branch_comparison_report.py) prepares **already-computed BE-47 summaries from branches of the same business**, requiring compatible dates/currency/unit/value basis and caller-established report definitions. It preserves `Decimal` representations and counts, returns branch-ordered independent results, and **does not** rank branches, calculate growth/differences or invent missing zero-valued rows. **Recorded new tests:** 155. **Recorded commit:** `e6f0fd6`. **Remaining:** trusted multi-branch RBAC, equivalent data definitions, approved KPIs, inventory snapshot alignment, Flask/export.

### BE-50 — Report Filtering & Export APIs, Phases 1 and 2

**Phase 1:** [`report_export.py`](src/branchwise/reports/report_export.py) implements `export_report_csv(rows, columns)`, returning UTF-8 BOM CSV **bytes** from supplied rows/columns, with selected-field ordering, proper CSV quoting, Arabic/Unicode preservation and protective handling of spreadsheet-formula-like **text**. The function is in-memory, no file, Flask or DB access. The trusted caller must approve selected columns; CSV mitigation is not a substitute for downstream spreadsheet/client review.

**Phase 2:** extended `_csv_cell()` to accept only **finite `Decimal`** values, serialized with `str(value)`; e.g. `Decimal("12.5000")` exports exactly as `12.5000`, without float conversion or arithmetic. Tests now cover precision, negative zero, exponent form, invalid `NaN`/infinities, input immutability and Decimal-context behavior. Prior tests rejecting Decimal were updated appropriately; other unsupported types remain rejected. **Recorded export tests:** 59 at Phase 1, **69 after Phase 2**. **Recorded Phase 2 commit:** `c46305f`.

**Remaining:** approved report columns/filter semantics, authenticated export endpoints, real report integration, access checking, optional additional export formats and relevant integration tests. **BE-50 is PARTIAL.**
### BE-28 Phase 2 — Provisional Supplier API

Added `backend/supplier_routes.py` with an isolated Flask Blueprint
for `POST /api/suppliers`.

The endpoint:
- Validates JSON request bodies.
- Uses the existing `validate_supplier_input()` helper.
- Rejects unknown fields, including client-supplied `business_id`.
- Returns HTTP 400 for invalid input.
- Returns HTTP 501 for valid input because authentication and
  database persistence are not yet integrated.

Added `backend/tests/test_supplier_routes.py` containing 7 passing
API tests.

The Blueprint is NOT registered in the main `backend/app.py`.
There is no actual supplier creation, database write, authentication,
or authorization in this provisional implementation.

Do not activate the endpoint for real supplier creation until
authentication, tenant authorization, approved database schema,
repository implementation, and integration tests are available.

Commit: `f8221e4`
Branch: `backend-dev2`
GitHub push: Successful
Historical full backend test suite after BE-28 Phase 2: 1,566 passed, 0 failed.

BE-28 remains PARTIAL.

### BE-28 Phase 3 — Supplier API Integration Readiness (historical pre-merge audit)

The dependency observations below describe the original Developer 2 workspace. They are superseded for the merged integration worktree by the current preparation-service update at the end of this document; historical notes are retained for context.

**Already implemented and tested:** `supplier_validation.py` performs basic required-name, optional-string, and email-syntax checks. The isolated `supplier_bp` at `POST /api/suppliers` rejects non-JSON/non-object bodies and unknown request fields, including client-supplied `business_id`; invalid input returns HTTP 400 and structurally valid input returns HTTP 501. Seven route tests cover these provisional behaviors. The Blueprint remains unregistered in `app.py`.

**Verified available dependencies:** Flask is used by the route, and `mysql-connector-python` is listed in `backend/requirements.txt`. The current `app.py` only exposes `/api/health`. These facts do not provide an authentication integration or usable database persistence contract.

**Missing or unapproved dependencies:** No usable authentication/RBAC interface, supplier model/schema, repository, or supplier service was found in the outer backend workspace; `db.py` is empty. The schema fields and nullability, business ownership relationship, uniqueness scope, field limits, duplicate-name behavior, optional blank/`None` normalization, and create-response/error contract are not approved here. The validator's names and optional-field behavior remain provisional and do not define database columns.

**Blocked integration checklist (do not replace HTTP 501 yet):**

1. **Developer 1 / authentication owner:** provide the trusted authentication context and authorization interface, including how the actor's business scope and supplier-create permission are established. Never accept client-supplied identity, role, permission, or `business_id` as authority.
2. **Developer 1 / database owner:** approve the supplier schema and business relationship, constraints and uniqueness scope, lifecycle/deactivation rules, and the repository/service interface and transaction behavior. Confirm deployment configuration and migrations separately; an installed connector alone is not persistence integration.
3. **Team/API owners:** approve request/response fields, optional-value semantics, field-length and phone policies, duplicate handling, and HTTP status/error mapping. Align validation with the approved storage constraints without silently normalizing or discarding input.
4. **Developer 2, after contracts are supplied:** adapt the route to authenticate, authorize the trusted business scope, validate only the approved allowlisted payload, call the approved persistence service, and return the approved response. Keep `business_id` server-derived rather than client-controlled.
5. **Developer 1 and Developer 2 together:** add integration tests for unauthorized and cross-business requests, persistence success/failure and duplicate conflicts, transaction behavior, and response disclosure. Only after these pass and ownership agrees should the Blueprint be considered for main-app registration.

Until those dependencies and decisions are provided, no real supplier creation, authentication, authorization, or database write is available; keep the provisional Blueprint unregistered and BE-28 **PARTIAL**. These readiness notes do not change Developer 1 ownership of authentication or database contracts.

## 5. Transfer tasks (BE-34 through BE-37)

### BE-34 - Stock Transfer Request structural validation

**Implemented:** `validate_transfer_request_input(data)` in new file `backend/transfer_request_validation.py`, with new tests in `backend/tests/test_transfer_request_validation.py`.

Validation covers required branch IDs, distinct source/destination branches, nonempty items, positive integer variant IDs and quantities, unknown field rejection, and input immutability.

**Reported validation:** **87 targeted tests passed**; **1,889 full backend tests passed**. **Latest pushed commit:** `197e802` on `backend-dev2`.

**Remaining blockers:** trusted permissions, authoritative stock checks, persistence, transactions, server-generated metadata, duplicate-line policy, and approved lifecycle rules. Structural validation does not establish production transfer creation or inventory safety. **BE-34 remains PARTIAL, not production-complete. BE-35 remains PARTIAL; BE-36 remains PARTIAL; BE-37 remains PARTIAL.**

### BE-35 - Transfer Approval & Rejection structural validation

**Created files:** `backend/transfer_decision_validation.py` and `backend/tests/test_transfer_decision_validation.py`.

`validate_transfer_decision_input(data)` returns field errors using a **provisional internal input contract**, not an approved production API or persistence contract. It validates a positive built-in integer `transfer_id` (excluding booleans), accepts exact `approve` and `reject` actions, rejects all unknown fields including client-controlled actor identity, role, permissions, ownership, trusted timestamps and transfer state, and preserves input immutability. These structural checks do not establish authorization. No optional decision fields are specified by the available transfer documentation, so none are accepted; no rejection-reason requirement is invented.

**Reported tests:** **108 targeted tests passed**; **1,997 full backend tests passed**. **Latest pushed implementation commit:** `b04a945` on `backend-dev2`.

**Remaining blockers:** trusted transfer lookup and ownership; authentication and authorization; approved lifecycle and API contracts; persistence and transactions; server-generated metadata; dispatch and inventory integration. The validator performs no approval/rejection operation, status update, or inventory mutation, and no production route is registered by this implementation. Developer 1's lifecycle and quantity rules are not duplicated.

**BE-35 remains PARTIAL. BE-34 remains PARTIAL. BE-36 remains PARTIAL; BE-37 remains PARTIAL.**

### BE-36 - Transfer Receiving structural validation

**Created files:** `backend/transfer_receiving_validation.py` and `backend/tests/test_transfer_receiving_validation.py`.

`validate_transfer_receiving_input(data)` uses an **explicitly provisional internal contract**, not an approved API or database contract. It validates transfer identification, requires a nonempty item list, validates variant identifiers and positive integer quantities, rejects unknown fields and client-controlled trusted fields, preserves input immutability, and returns field-specific errors.

**Reported tests:** **135 targeted tests passed**; **2,132 full backend tests passed** (reported by the user; not rerun for this documentation update). **Latest pushed commit:** `b1ee979` on `backend-dev2`.

**Remaining blockers:** approved API and database contracts; trusted transfer lookup and authorization; inventory availability and receiving verification; discrepancy and completion policies; atomic inventory updates, transactions, and durable idempotency. Structural validation does not establish real receiving, discrepancy settlement, inventory safety, or production readiness.

**BE-34, BE-35, and BE-36 remain PARTIAL. BE-37 remains PARTIAL.**

### BE-37 - Transfer Cancellation, Phase 1

Created `backend/transfer_cancellation_validation.py` and `backend/tests/test_transfer_cancellation_validation.py`.

`validate_transfer_cancellation_input(data)` validates a provisional internal dictionary envelope containing a required positive built-in integer `transfer_id`. It rejects booleans, integer subclasses, malformed identifiers, and every unexpected field, including client-controlled user identity, roles, permissions, business ownership, transfer status, server-generated timestamps, and inventory movements. It returns field-specific errors consistent with the existing Dev2 validators and preserves input immutability. No cancellation reason is required because no approved requirement was found; no optional cancellation fields are introduced.

**Reported tests:** **99 targeted tests passed**; **2,231 full backend tests passed** (reported by the user; not rerun for this documentation update). **Latest pushed commit:** `0e9a351` on `backend-dev2`.

**Remaining blockers:** approved cancellation eligibility and post-dispatch policies; trusted transfer lookup and authorization; approved lifecycle transitions; stock reversal behavior; atomic transactions and durable idempotency. Structural validation does not establish that Pending, In Transit, Completed, or Rejected transfers can be cancelled. It performs no cancellation, stock restoration/reversal, persistence, or production route registration and does not establish production readiness.

**BE-34, BE-35, BE-36, and BE-37 remain PARTIAL.**

### Transfer integration review and outstanding decisions

The earlier compatibility review remains relevant. A read-only review examined the cached `origin/backend-dev1` version of `backend/transfer_rules.py` and its tests. That module contains pure branch/item validation, dispatch/receipt quantity and discrepancy calculations, and lifecycle transition checks; duplicating it on Dev2 would create unnecessary ownership conflicts.

**Two team decisions must be resolved before implementing transfer services:**

1. Developer 1's test allows `In Transit -> Completed` for dispatched=5, received=4, remaining discrepancy=1. BW-10 requires discrepancy handling without stock loss/duplication, but the approved completion/closure decision and discrepancy settlement contract must be confirmed from the authoritative project/database decision record (including any D-04 text).
2. Developer 1's transition rules **disallow** `In Transit -> Cancelled`. Confirm how BW-10's post-dispatch authorized cancellation and quantity reconciliation are intended to work, including states, durable movement records and transaction ownership.

BE-34 request API integration remains blocked beyond structural validation; BE-35 approval/rejection integration and dispatch remain blocked beyond structural validation; BE-36 receiving/discrepancy integration remains blocked beyond structural validation; BE-37 cancellation/idempotency remains PARTIAL with structural cancellation-input validation only; **no stock balance changes or transfer endpoints should be implemented without approved schema/state, authoritative stock (`branch_inventory.available_qty` in earlier project notes), atomic movement-ledger semantics, trusted permissions and duplicate protection**. The original approved D-02/D-04 decision text should be verified with the team rather than inferred from provisional helpers.

## 6. Cross-cutting integration boundaries and dependencies

- **Authorization:** use Developer 1's trusted authentication/RBAC when integrated. Supplied business, branch, supplier, user IDs and permission flags are **not proof of access**. Verify every source/target resource for the selected action.
- **Data ownership:** obtain approved schema/contracts from the database teammate; do not invent SQL columns or overwrite `backend/db.py` and Developer 1 code. The outer `backend/app.py` previously exposed only `/api/health`, and `backend/db.py` was empty at inspection; re-check the current integrated branch before wiring anything.
- **Money:** prefer exact `Decimal` and agreed JOD/currency, rounding/tax/discount semantics. Do not calculate spending from supplier quotes or infer net-sales/return policies.
- **Inventory:** reads/changes need authoritative availability and an append-only movement ledger. Receiving, dispatch, cancellation and duplicate detection must be coordinated in **durable atomic transactions**, not by pure helpers or in-memory flags.
- **Dates and reporting:** align timezone, eligibility, source completeness, report definitions, snapshot cutoff, and unique source identities before cross-branch/historical comparisons. Missing records are not implicit zeros.
- **AI:** use trusted scoped inputs, safe error messages, no secrets in logs/output; verify model/provider access before enabling live calls. Tests with mocks do not prove live service availability.
- **Testing:** keep unit and isolated HTTP tests plus later DB transaction, concurrency/idempotency, authorization, and frontend/backend integration tests. These later integration suites are not yet covered by the last reported 1,790 local tests.

## 7. AI environment and dependency notes

These are the names documented by the existing BE-38 code; **do not commit actual secrets**:

| Variable | Current internal convention |
| --- | --- |
| `GEMINI_API_KEY` | Preferred nonblank API key |
| `AI_API_KEY` | Fallback if preferred key is absent/blank |
| `GEMINI_MODEL` | Optional model selector; the code's historically documented default is `gemini-3.8-flash` **(live validity unverified)** |
| `AI_TIMEOUT_SECONDS` | Optional, default `10` |
| `AI_MAX_INPUT_CHARS` | Optional, default `10000` |
| `AI_MAX_OUTPUT_CHARS` | Optional, default `20000` |

The AI modules read environment variables from the process; the existing `.env.example` lists Flask and DB names but did not yet contain all AI variables. Python's standard library is sufficient for most pure helpers. The Gemini adapter requires the pinned `google-genai==2.34.0` as recorded in the earlier handoff; verify dependencies/SDK contract before an upgrade or authorized live use. Flask and MySQL dependencies existing in `requirements.txt` do **not** establish working integration.

## 8. Local setup and tests (Windows PowerShell)

From the **OUTER repository root** (`C:\Users\bahao\OneDrive\Documents\GitHub\Branch-wise`), not the nested Git repository:

```powershell
# Only if setting up a new environment:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# Run the entire backend test suite without writing bytecode/pytest cache:
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -c backend\pytest.ini backend -q

# Example: run the BE-50 export tests alone:
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -c backend\pytest.ini backend\tests\test_report_export.py -q

# Check working branch and changes:
git status -sb
```

The latest reported full suite after BE-37 structural validation: **2,231 passed**; targeted BE-37 tests: **99 passed**. **Historical full suite after BE-36 structural validation:** **2,132 passed**; targeted BE-36 tests: **135 passed**. **Historical full suite after BE-35 structural validation:** **1,997 passed**; targeted BE-35 tests: **108 passed**. **Historical full suite after BE-34 structural validation:** **1,889 passed**; targeted BE-34 tests: **87 passed**. **Historical full suite after BE-53 Phase 5:** **1,802 passed, 0 failed**. **Historical result after BE-53 Phase 4:** **1,801 passed, 0 failed**. **Historical result after BE-48 Phase 2:** **1,800 passed, 0 failed**. **Historical result after BE-47 Phase 3:** **1,799 passed, 0 failed**. **Historical result after BE-47 Phase 2:** **1,798 passed, 0 failed**. **Historical result after BE-53 Phase 2:** **1,790 passed, 0 failed**. **Historical result after BE-41 Phase 2:** **1,778 passed, 0 failed**. **Earlier historical result after BE-45 Phase 2:** **1,762 passed, 0 failed**. **Earlier historical result after BE-30 Phase 2:** **1,740 passed, 0 failed**. **Earlier historical result after BE-29 Phase 2:** **1,650 passed, 0 failed**. **Earliest recorded historical result after BE-50 Phase 2:** **1,559 passed in 4.42 seconds** on the user's local machine. Rerun the suite if any code changes. Most unit tests do **not** need live Flask, MySQL, Gemini or network connectivity. Some explicitly guard against those external operations.

### BE-53 Phase 2 — Offline Forecast-to-Replenishment Pipeline Regression Tests

[`test_forecast_replenishment_pipeline.py`](tests/test_forecast_replenishment_pipeline.py) contains **12 passing tests** that exercise the real BE-39 → BE-45 → BE-41 pipeline, including BE-40 forecasting. Coverage includes missing dates, observed zero sales, unavailable forecasts, business/branch/variant isolation, duplicate daily observations, and input immutability.

These are offline tests using synthetic observations and stock inputs, not real MySQL, authentication, or inventory integration tests. **Commit:** `fe80806` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,790 passed, 0 failed**. Relevant implementation tasks remain PARTIAL; these tests do not alter Developer 1 ownership or integration boundaries.

### BE-53 Phase 3 — Provisional API Integration Regression Tests

[`test_provisional_routes_integration.py`](tests/test_provisional_routes_integration.py) adds **7 passing tests**: three verify that the provisional Supplier, Purchase Order, and Purchase Receiving Blueprints coexist in one isolated Flask app and each returns its own HTTP 501 response; one verifies all three endpoints remain unregistered in the main Flask app; and three guard known database and network side-effect boundaries for valid provisional requests.

These tests do **not** establish real authentication, authorization, persistence, or inventory safety. The provisional APIs remain unregistered and the relevant implementation tasks remain PARTIAL. **Commit:** `c7c8669` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,797 passed, 0 failed**. No Developer 1 ownership boundaries are changed.

### BE-53 Phase 4 — Sales Report Aggregation Consistency

[`test_sales_report_consistency.py`](tests/test_sales_report_consistency.py) adds **1 passing offline test** using synthetic observations and the real BE-47 report function. It verifies reconciliation of exact `Decimal` sales totals across overall, product, daily, and daily-product aggregations; quantity consistency; inclusive date filtering; and input immutability. It also confirms that distinct transaction counts must not be summed across overlapping product groups, because one transaction may contribute to multiple products.

The test does **not** verify real database access, authorization, or source completeness. It does not change BE-47's or other implementation tasks' PARTIAL status or Developer 1's ownership of authentication, database, and authorization contracts. **Commit:** `6c64622` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,801 passed, 0 failed**.

### BE-53 Phase 5 — Inventory Snapshot Consistency

[`test_inventory_snapshot_consistency.py`](tests/test_inventory_snapshot_consistency.py) adds **1 passing offline test** using synthetic observations and the BE-48 snapshot report function. It verifies that every accepted observation contributes to exactly one group, contribution counts reconcile with accepted observations, and group quantities reconcile by product, variant, quantity unit, and quantity basis. Incompatible units and bases remain isolated, and input observations remain unchanged.

The test does **not** establish real inventory completeness, source authenticity, authentication, or authorization. It does not change task PARTIAL statuses or Developer 1's ownership boundaries. **Commit:** `29d357c` on `backend-dev2`; **GitHub push:** successful. **Full backend suite at that point:** **1,802 passed, 0 failed**.

### BE-58 - Bug Fixing: confirmed BE-40 zero-demand forecast defect

A focused review confirmed a BE-40 defect: a zero-demand forecast with `forecast_days=10**400` raised `ValueError`. Fix commit `c290725` updated `backend/demand_forecast.py` to handle a zero daily average without converting an extremely large forecast period to float. That forecast now returns `0.0`.

Two regression tests were added to `backend/tests/test_demand_forecast.py`. **Reported targeted tests: 113 passed. Reported full backend suite: 2,233 passed.** These results were supplied by the user and were not rerun for this documentation-only update.

This was a **focused review, not an exhaustive audit of BE-28 through BE-50**. BE-58 remains ONGOING. **BE-40 remains PARTIAL because real-data API integration remains unfinished.**

### BE-59 - Documentation: BE-40 fix record

This update records the confirmed BE-40 defect, fix commit `c290725` (latest pushed commit reported by the user on `backend-dev2`), two regression tests, and the reported **113 targeted / 2,233 full backend tests passed**. It supplements the earlier review and test history above; earlier counts and review observations describe their respective snapshots. BE-59 remains IN PROGRESS, and all existing task statuses and integration boundaries remain unchanged.

### BE-57 - Postman Collection (BE-59 documentation record)

**Commit:** `53d711f` on `backend-dev2` (latest pushed commit reported by the user).
Created files (repository-relative paths):

- [`backend/postman/BranchWise.postman_collection.json`](postman/BranchWise.postman_collection.json)
- [`backend/postman/README.md`](postman/README.md)
- [`backend/tests/test_postman_collection.py`](tests/test_postman_collection.py)

The Postman Collection is valid and importable as Collection v2.1. Its
`base_url` variable defaults to `http://127.0.0.1:5000`. The registered
`GET /api/health` request is runnable against the existing local app and
checks HTTP 200 with `{"status": "ok"}`.

Supplier (`POST /api/suppliers`), Purchase Order (`POST /api/purchase-orders`)
and Purchase Receiving (`POST /api/purchase-receiving`) requests are in a
separate **NOT REGISTERED / NOT IMPLEMENTED** folder. They remain unregistered
in the main application, where these paths return HTTP 404. The inherited
`pm.execution.skipRequest()` guard disables them by default in supported
Postman runtimes; older clients must not be relied on to skip requests.

Valid and invalid JSON examples reflect existing validators and route tests.
Only an isolated app registering the existing Blueprints produces the
illustrated HTTP 400/501 behavior: invalid fields return 400 with `errors`,
while non-JSON, malformed or non-object bodies return 400 with `error`.
Structurally valid examples return 501 with a pending-integration message.
Purchase costs use exact decimal strings; JSON floats fail validation.
Synthetic IDs imply no database records. The collection does not demonstrate
live authentication, authorization, database persistence or inventory changes.

Remaining gaps are documented without invented HTTP requests:

| Tasks | Remaining API integration |
| --- | --- |
| BE-31 | Purchase history/supplier record routes, scoped repositories and response contracts |
| BE-34-37 | Transfer request, approval/dispatch, receipt/discrepancy and cancellation routes; approved lifecycle, stock ledger, atomic transactions and durable idempotency |
| BE-38 | Trusted AI configuration, provider integration and approved route contracts |
| BE-39-42 | Sales history, forecast, replenishment and alternative comparison routes with authorized real inputs and approved rules |
| BE-43-45 | Business questions, decision logs and reliability/fallback integration; scoped context, persistence and response contracts |
| BE-46-50 | Dashboard, sales/product, inventory/purchase, branch comparison and export routes; approved definitions, complete sources, filters and export contracts |

All require coordination with Developer 1 for trusted authentication,
authorization and approved database contracts. All existing PARTIAL task
statuses remain unchanged; BE-57 completes the independent collection artifact
and does not establish production readiness.

**Recorded verification:** valid Postman JSON and whitespace checks passed;
**9 targeted tests passed** and **2,242 full backend tests passed**. These
results were confirmed by the user for commit `53d711f`; tests were not rerun
for this documentation-only update. Historical results above retain their
original snapshot meaning. BE-59 remains IN PROGRESS.

## 9. Collaboration and next steps

1. Share this handoff with the team and confirm task boundaries; **do not merge/cherry-pick Developer 1's transfer helpers without an ownership/contract discussion**.
2. Confirm real application endpoint, database/entity, and authentication contracts for BE-28–31, BE-38–50. Implement routes and services in agreed ownership areas, not as independent guessed APIs.
3. Confirm BW-10 stock-transfer completion/discrepancy and cancellation contracts with Developer 1/database teammate before BE-34–37.
4. Implement integration tests for authorization, transactions, duplicate requests and real data flow; document real run/deploy configuration only once tested.
5. Keep changes limited to authorized files, run tests before committing and use isolated commits on `backend-dev2`. Never `--force` push to resolve ordinary branch issues.

**Documentation state:** This document updates the previous BE-59 handoff (which described only BE-28 and BE-38). It does not claim production deployment or completion of any of the 21 partial main tasks.


### BE-28 - Internal supplier preparation service (merged integration update)

`src/branchwise/suppliers/supplier_service.py` now exposes
`prepare_supplier_create(data, *, actor: AuthenticatedSession, scope: ResourceScope)`.
This internal service requires a server-resolved Developer 1 session, positive
user identity and authorized business scope. It delegates authorization to
`require_resource_access` with `suppliers:manage`: only an Owner with the trusted
grant can prepare creation; Branch Managers and Employees remain denied even
if supplied that grant. Actor and scope must never originate in request fields.
Session expiry/current-account verification stays with `SessionManager`; future
HTTP callers must use the established session/CSRF decorator.

The service reuses `validate_supplier_input` unchanged and the shared allowlist
helper (`errors.body` convention) to reject unknown fields without reflecting
metadata. Its frozen `SupplierPreparation` contains only a read-only `errors`
mapping. Empty errors mean preparation passed, never persistence success. No
normalization, duplicate policy, schema constraint or lifecycle rule was added.

`tests/test_supplier_service.py` covers authorization, missing identity/grants,
invalid or foreign business scope, role restrictions, forged request metadata,
field validation, optional-value behavior, input immutability and blocked
network/database operations. These are internal preparation unit tests, not
production API integration tests.

Current merged infrastructure includes `backend/db.py` SQLAlchemy/Migrate
extensions, `session_auth.py`, `permissions.py`, `isolation_rules.py` and typed
repository conventions. The historical statements that db.py is empty or that
only health is exposed do not describe this merged worktree. Persistent auth,
session and application repository adapters are not installed by default.

BE-28 remains PARTIAL: production CRUD and persistence are incomplete. Supplier
model/schema, repository and API contracts still require approval, including
field limits, optional-value storage, uniqueness and lifecycle semantics. The
supplier blueprint remains unregistered in the main application; its existing
isolated provisional route and validator are unchanged. No production supplier
endpoint, repository, ORM model or migration was added.

Verification: 91 supplier service/validation/provisional-route tests passed; full
merged backend suite: 3,416 passed (baseline 3,383 plus 33 service tests).
The main Flask route map matches HEAD exactly: 49 rules, no supplier endpoint.

### BE-29 - Secure internal purchase-order input preparation (merged integration update)

`src/branchwise/purchasing/purchase_order_service.py` now exposes
`prepare_purchase_order_input(data, *, actor: AuthenticatedSession, branch_scope: ResourceScope, business_branch_count: int) -> PurchaseOrderPreparation`.
This is an internal preparation service. The actor must be resolved by Developer
1's SessionManager; destination scope must be established by the server, and the
branch count must be loaded for that business. Request metadata supplies none of
these trusted values. Session expiry/current-account checks remain with the
session manager; future HTTP callers must also use its session/CSRF decorator.

Existing `require_resource_access` and `is_branch_operation_ready` enforce the
role matrix, trusted grants, business isolation, assigned manager branch and
onboarding readiness. Owners require `purchases:manage`; Branch Managers require
`purchases:manage_authorized` and their assigned destination branch. Employees
remain denied even with forged grants. Invalid/foreign scopes, zero branches and
a structurally valid request branch that differs from the trusted destination
raise the shared non-disclosing PermissionError. Invalid trusted branch counts
retain the onboarding helper's ValueError; trusted context arguments are required.

The existing `validate_purchase_order_input` is reused without numerical or
structural rule changes. Missing/malformed request IDs return its field errors.
The provisional route's root/item allowlists now live in the validator module as
immutable frozensets and are reused by both callers. Unknown fields, including
forged roles, business IDs, grants, totals and statuses, are rejected without
reflecting their values. Caller data and Decimal context remain unchanged.
The provisional route retains its existing HTTP 400/501 behavior.

The frozen `PurchaseOrderPreparation` contains only a copied, read-only errors
mapping. Empty errors mean preliminary authorization and structural checks passed.
Supplier, branch, product and variant existence/eligibility are not verified by
this service; authoritative price lookups remain unresolved. No order identity,
reference, totals, status, creation command, database writes or inventory changes
are produced. Empty errors do not establish production persistence readiness.

`tests/test_purchase_order_service.py` covers trusted sessions, Owner and Branch
Manager permission boundaries, Employee denial, scope and onboarding failures,
reused validation, forged metadata, Decimal context, input/result immutability
and blocked repository, inventory, database and network operations.

BE-29 remains PARTIAL. Approved supplier/branch/catalog reference lookups and
authoritative prices are still required. PO/line models, repository and API
contracts, transaction ownership, reference/duplicate-line rules, product/variant
compatibility and lifecycle policies still require approval. No ORM model,
migration, production repository or Purchase Order endpoint was added. The
Purchase Order Blueprint remains unregistered in the main application.

Verification in the isolated integration virtual environment: 214 new service
tests passed; 408 existing purchasing, permission/isolation/onboarding, supplier
and provisional-route regression tests passed. Full merged backend suite:
**3,630 passed** (3,416 baseline plus 214 new tests). Tests used an explicit
temporary directory outside the worktree for the known Windows permissions
issue. The main Flask route map matches the pre-change snapshot exactly:
**49 rules**, with no Purchase Order endpoint. Existing validator function ASTs
also match HEAD exactly.

### BE-30 - Secure internal purchase-receiving input preparation (merged integration update)

`src/branchwise/purchasing/purchase_receiving_service.py` now exposes
`prepare_purchase_receiving_input(data, *, actor: AuthenticatedSession, branch_scope: ResourceScope, business_branch_count: int) -> PurchaseReceivingPreparation`.
This internal preparation service requires a session resolved by Developer 1's
SessionManager, a destination scope established by the server, and a branch
count loaded for that business. Request metadata cannot supply trusted context.
Session expiry, current-account checks and access-version validation remain
with SessionManager; future HTTP callers must use the existing session/CSRF
decorator.

Authorization precedes detailed input validation. Existing
`require_resource_access` and `is_branch_operation_ready` enforce the role
matrix, trusted grants, business isolation, destination scope and onboarding.
Owners require `purchases:manage`; Branch Managers require
`purchases:manage_authorized` and assignment to the destination branch.
Employees remain denied even with injected purchasing grants. Invalid or
foreign context, branchless destinations and zero branches raise the shared
non-disclosing PermissionError. Invalid trusted branch counts retain the
onboarding helper's ValueError; trusted arguments are required and keyword-only.

The existing `validate_purchase_receiving_input` function is reused unchanged.
Its provisional built-in integer checks, zero-quantity acceptance and repeated
line behavior remain structural checks only, without an approved receiving
policy. Shared immutable root/item allowlists are reused by the service and
provisional route. Unknown fields and forged identity, scope, status and
transaction metadata are rejected without reflecting names or values.
Inputs remain unchanged; errors stay deterministic.

The frozen `PurchaseReceivingPreparation` contains only a copied, read-only
errors mapping. Empty errors mean preliminary authorization and structural
checks passed ONLY. Checking the supplied trusted destination scope does not
verify that the submitted purchase order exists, belongs to that business or
has that destination. The service does not verify line membership, remaining
receivable quantities or order eligibility. It generates no receipt ID,
timestamp, stock movement, status, persistence command or successful-receiving
response, and makes no committed-transaction or durable-idempotency claim.

`tests/test_purchase_receiving_service.py` covers role/grant and trusted-session
boundaries, business/branch isolation, onboarding, authorization ordering,
existing validation semantics, forged metadata, deterministic errors,
input/result immutability and guarded database, repository, network, inventory
and transaction boundaries.

BE-30 remains PARTIAL. Approved persisted PO/line and supplier/catalog lookups,
actual destination verification, receiving lifecycle/quantity/discrepancy/cost
policies, durable receipt identities, classified stock movements, atomic
receiving transactions and durable replay protection remain blocked.
The main application still does not register the receiving blueprint. The
isolated provisional route preserves its existing 400/501 responses.
No model, migration, production repository or persistent receiving operation
was added.

Verification in the isolated integration virtual environment: **204 new
receiving-service tests passed**; **897 existing regression tests passed**,
including receiving validation/provisional routes, BE-29 preparation,
permissions/isolation/onboarding/session/transaction/inventory foundations and
BE-54 provisional integration. Full merged backend suite: **3,834 passed**
(3,630 baseline plus 204 new tests), using an explicit temporary directory
outside the worktree. The main Flask route map matches HEAD exactly:
**49 rules**, with no receiving endpoint (404). The receiving validator function
and provisional route handler ASTs match HEAD exactly. Whitespace checks passed.

### BE-28 - Candidate supplier persistence adapter against supplied SQL

**Status: CANDIDATE integration implementation; not production ready.** The
original supplied SQL at C:/Users/bahao/Downloads/BranchWise database.sql,
lines 336-362, was inspected. Its supplier definition is identical in the
repaired DRAFT, but final schema-owner approval remains unconfirmed. The
proposed inventory tables/policies in that DRAFT are not accepted by this work.

**Verified supplied supplier DDL:** unsigned auto-increment BIGINT primary ID,
required unsigned business ID, required name VARCHAR(160), nullable
contact_person VARCHAR(160), phone VARCHAR(30), email VARCHAR(254),
address VARCHAR(255) and notes TEXT; non-null active flag defaulting to 1;
non-null DATETIME(6) timestamps with server creation/update defaults.
Named unique keys are uq_suppliers_bid_id (business_id, id) and
uq_suppliers_name (business_id, name), with ck_suppliers_name checking a
nonblank trimmed name and fk_suppliers_business_id referencing businesses.id.
Table engine/encoding/collation are InnoDB/utf8mb4/utf8mb4_0900_ai_ci.

**Implementation:**

- src/branchwise/suppliers/supplier_model.py: SQLAlchemy Core suppliers
  mapping using the existing db.metadata, with MySQL unsigned identifiers,
  microsecond timestamps, exact named constraints and server defaults. No
  independent engine, ORM base, migration, reflection or partial production
  businesses model was introduced. The FK references the existing business
  table. Runtime Core insert/select does not require a competing business
  mapping; any future metadata DDL generation would require its real mapping.
- supplier_contracts.py: frozen SupplierCreateData and detached
  SupplierRecord, typed SupplierRepository port, and safe field errors.
  The port follows Developer 1's trusted-session and atomic-method conventions
  and uses the existing shared repository exception hierarchy.
- supplier_repository.py: SQLAlchemySupplierRepository(session_factory)
  implements create_supplier_atomic(actor=..., data=...) and
  get_supplier(actor=..., supplier_id=...). The injected factory must return
  a fresh idle SQLAlchemy Session, e.g. sessionmaker(bind=db.engine) inside
  an app context. It owns each session/transaction, closes its sessions and
  returns creation success only after commit. It rejects a caller's active
  transaction without committing, rolling it back or closing it.
- supplier_persistence.py: internal create_supplier(data, *, actor, scope,
  repository) invokes the unchanged prepare_supplier_create before typed
  SQL-limit validation and repository access. There is no HTTP wiring.

Creation requires a trusted Owner with suppliers:manage. Lookup requires the
existing role/grant checks for suppliers:view using the actor's own business
and assigned branch context (Owner or permitted Branch Manager). Business
identity always comes from the trusted actor. Every supplier read, including
the read after insertion, predicates on both business ID and supplier ID.
Foreign/missing IDs produce the same safe not-found error. No request-supplied
business/status/ID/timestamps are supported. Only supplier rows are written.
No update/delete/status-transition methods or normalization were introduced.

Uniqueness is decided by the database, without a preflight name lookup or
Python name folding. MySQL duplicate error 1062 is classified as a name
conflict only for uq_suppliers_name; SQLite tests recognize that exact
column-pair constraint. Other integrity/SQLAlchemy failures become generic
persistence errors with suppressed SQL exception chaining. Failed insert,
post-insert lookup and commit paths roll back; no success escapes them.

**Validation compatibility:** the existing pure preparation/validation helpers
remain unchanged. They deliberately do not enforce SQL sizes. The candidate
typed write boundary adds the supplied VARCHAR character limits and the MySQL
TEXT capacity of 65,535 UTF-8 bytes, counting the original untrimmed value.
Invalid Unicode is rejected before database access. Missing optional values
become SQL NULL; explicit NULL, empty and whitespace strings are preserved.
The preparation helper rejects Python-whitespace-only names more strictly
than SQL's space-based TRIM check; that existing behavior is preserved. Its
basic email syntax check also remains in place. No application-level name
collation is invented: utf8mb4_0900_ai_ci is case/accent insensitive and uses
MySQL's comparison rules, which SQLite's default binary collation does not
replicate.

**Executed verification (existing BranchWise_INTEGRATION_VENV):**

- Candidate supplier repository tests plus existing supplier preparation,
  validation, provisional route and provisional integration tests: 163 passed.
- Developer 1 permissions, session authentication, isolation, repository
  contracts and database configuration tests: 199 passed.
- Complete backend suite: 3887 passed.
- Flask route map compared with the pre-change snapshot: 49 rules, unchanged;
  /api/suppliers remains unregistered.
- git diff --check: passed after implementation.

tests/test_supplier_repository.py performs actual SQL insert/select/constraint
and rollback checks against isolated disposable SQLite files across independent
sessions. Test-only parent/sentinel tables verify foreign keys and absence of
unrelated writes. Test metadata substitutes portable SQLite timestamp defaults
and registers CHAR_LENGTH; SQLite's native primary/unique/non-null/check/FK
constraints are exercised. SQL VARCHAR limits are tested at the typed adapter
boundary, since SQLite does not enforce VARCHAR lengths. MySQL DDL compilation
and Connector/Python error simulations are static/unit checks only.

**Unverified:** no live MySQL integration, utf8mb4_0900_ai_ci comparison behavior,
unsigned-range enforcement, DATETIME(6)/ON UPDATE execution, MySQL TEXT/VARCHAR
storage behavior, concurrent duplicate insertion, or MySQL transaction behavior
is claimed. No DDL was executed against an existing/shared/production database.

**Remaining production blockers:** final schema-owner approval, isolated MySQL
compatibility/concurrency verification, approved deployment/session/grant
contracts and installation of persistent authentication/session/user-loader
adapters. No supplier repository installer or production factory wiring was
added. The existing unprotected provisional supplier Blueprint remains
unregistered. BE-28 remains PARTIAL; no production Supplier API is enabled.


### BE-29 - Approved purchase-order mappings and read-only repository foundation

**Status: database mappings and scoped read foundation implemented; BE-29 remains
PARTIAL. Atomic creation and production API integration remain blocked.**

The database owner has confirmed the original supplied
`C:/Users/bahao/Downloads/BranchWise database.sql` as the final intended design.
This confirmation supersedes the earlier schema-approval caveats above for that
original design. The repaired DRAFT was not used. Only the original DDL was
read; its unrelated incomplete definitions were not executed. No migration,
physical schema change, shared database access or inventory change was made.

**Approved existing physical schema (original SQL lines 367-464):**

- `purchase_orders`: unsigned auto-increment BIGINT primary ID; required unsigned
  business, branch, supplier and ordering-user IDs; required purchase reference
  VARCHAR(40), unique per business; required ENUM status PENDING,
  PARTIALLY_RECEIVED or RECEIVED, default PENDING; required DATETIME(6) ordered_at,
  created_at and updated_at with database timestamp defaults and ON UPDATE for
  updated_at; required nonnegative expected_total_cost DECIMAL(14,2).
  Named business/id and business/reference unique keys and both approved
  business/branch/time/status and business/supplier/time indexes are mapped.
  Foreign keys reference businesses directly and branches, suppliers and users
  through their composite (business_id, id) keys.
- `purchase_order_items`: required unsigned auto-increment BIGINT primary ID,
  business/order/variant IDs and INT UNSIGNED ordered_qty; required
  DECIMAL(14,2) unit_cost and line_total. The approved named constraints enforce
  positive quantity, nonnegative unit cost,
  line_total = ROUND(ordered_qty * unit_cost, 2), unique (business_id, id) and
  unique (purchase_order_id, variant_id). The business/variant index and direct
  business FK plus composite business/order and business/variant FKs are mapped.
- Both mappings preserve InnoDB, utf8mb4 and utf8mb4_0900_ai_ci, exact constraint
  names, nullability and default behavior. No cascade policy was added.

**Files and operations:**

- `src/branchwise/purchasing/purchase_order_model.py` adds SQLAlchemy Core
  `purchase_orders` and `purchase_order_items` mappings using existing
  `db.metadata` and the supplier mapping's identifier/timestamp conventions.
  Parent FK strings reference existing tables; there are no competing partial
  parent models, new engines, reflection or import-time database operations.
- `purchase_order_contracts.py` adds the typed read-only PurchaseOrderRepository
  protocol, PurchaseOrderStatus literal and frozen detached PurchaseOrderRecord
  and PurchaseOrderItemRecord values. Monetary fields are Decimal.
- `purchase_order_repository.py` adds
  SQLAlchemyPurchaseOrderRepository(session_factory) with
  `get_purchase_order(actor=..., purchase_order_id=...)`,
  `get_purchase_order_item(actor=..., purchase_order_id=..., item_id=...)` and
  `list_purchase_order_items(actor=..., purchase_order_id=...)`.
  Lists return an immutable tuple ordered by item ID. An accessible empty order
  returns (); an absent or inaccessible parent raises safe not-found.
- `tests/test_purchase_order_repository.py` exercises the mappings, repository
  and database constraints against disposable SQLite files; MySQL compilation
  checks are static only.

Each read authorizes a server-resolved AuthenticatedSession before opening a
session. It reuses the existing isolation/role/grant helpers used by BE-29
preparation: Owners require purchases:manage; Branch Managers require
purchases:manage_authorized and are limited to their assigned branch; Employees
are denied even with forged purchasing grants. All queries derive business_id
from the trusted principal. Item reads additionally join the parent using both
business and order IDs and apply the parent's authorized branch predicate.
Foreign-business, foreign-branch, mismatched-parent and absent IDs cannot return
records. Missing and inaccessible resources have identical not-found behavior.
Malformed IDs fail before opening a session. Database failures become generic
RepositoryPersistenceError messages with SQL exception chaining suppressed.

The adapter follows the supplier session-ownership convention: inject a factory
returning a fresh idle SQLAlchemy Session, for example sessionmaker(bind=db.engine)
inside an application context. Each read owns and closes its session/transaction;
the result escapes only after successful transaction exit. Failed query and
transaction-exit paths roll back. A caller-owned active/pending unit of work is
rejected without committing, rolling back or closing it. Reads emit SELECT only.
No shared db.session is accepted as a caller-owned transaction. Detached results
contain no RowMapping, session, ORM instance or database exception.

The mappings use DECIMAL(14,2, asdecimal=True); no application float conversion,
price inference or total recomputation is introduced. Exact stored cents from
0.00 through 999999999999.99 are covered, including low caller Decimal precision
and unchanged Decimal context flags. SQLite's numeric storage is not MySQL
fixed-point enforcement; these tests do not approve extra-scale input handling.

**Atomic creation is intentionally unsupported.** The existing secure
prepare_purchase_order_input service and provisional validation/routes are
unchanged. Preparation still returns preliminary errors only and verifies no
supplier/catalog existence or authoritative prices. Its successful result is
not a creation command. For example, product-only lines and costs with more than
two decimal places can pass preliminary validation but cannot yet establish an
approved database write. No create, update, delete, status transition or receiving
write method is exposed by this repository.

Remaining decisions before creation/production integration:

- Authoritative purchase-cost source and whether/how a submitted purchase_cost
  participates; supplier/branch/catalog existence and activity eligibility.
- Product-only input to variant resolution and product/variant compatibility.
  The approved physical table always requires variant_id. Duplicate variants
  are already forbidden by the database; any input consolidation policy needs
  an explicit decision rather than silently merging lines.
- Purchase-reference allocation/format, concurrent uniqueness handling and retry
  behavior. VARCHAR(40) and per-business uniqueness alone do not specify a
  generator.
- Monetary scale validation versus rounding, rounding mode/stage, overflow
  handling and authoritative header-total calculation. The approved line CHECK
  does not define application handling of overprecision or enforce a header sum.
- Atomic creation command boundaries, revalidation/locking and creation
  idempotency/retry policy. Existing transaction_safety_rules cover receiving
  and other inventory/sales operations; they define no purchase-order creation
  operation. This foundation adopts read-session ownership only and invents no
  write transaction policy.
- Lifecycle/receiving transitions and concurrency, isolated live MySQL checks,
  approved production authentication/session/grant wiring and API contract.

**Production registration remains unchanged:** no installer/factory wiring or
production Purchase Order endpoint was added. The provisional Purchase Order
Blueprint remains unregistered. Session resolution, expiry/current-account
checks and future HTTP CSRF checks remain with the established authentication
layer. Existing BE-29 preparation keeps its onboarding readiness checks; read
lookups do not create orders or replace those checks.

**Verification (isolated BranchWise_INTEGRATION_VENV):**

- New repository suite: **135 passed**. Covers exact mappings/defaults/indexes,
  supported stored statuses, independent-session retrieval, immutable records,
  same-business lookups, manager branch restriction, cross-business and
  mismatched-parent isolation, Decimal boundaries/context, invalid IDs/FKs,
  unique references/duplicate variant lines, non-null/check constraints,
  rollback of a test-seeded header/lines transaction on duplicate lines,
  query/transaction-exit rollback and session closure, caller transaction
  preservation, authorization before session access, SELECT-only behavior and
  unchanged parent/inventory sentinel tables. The test-seeded rollback checks
  database atomicity; it does not claim a supported repository creation method.
- Existing Purchase Order validation/service/provisional routes: **339 passed**.
- Supplier repository/service/validation/provisional routes: **144 passed**.
- Full backend suite: **4,022 passed** (3,887 baseline plus 135 new tests).
- Exact Flask route comparison against the pre-change snapshot: **49 rules,
  unchanged**, including endpoints and methods; no Purchase Order endpoint.
- All databases used for new integration tests were disposable SQLite files
  under explicit --basetemp directories in the system temporary directory.
  Tests ran with PYTHONDONTWRITEBYTECODE=1 and -p no:cacheprovider.
- git diff --check: passed, including a separate check of new untracked files. Work remains unstaged and uncommitted on
  integration/dev1-dev2 at a34be9a; original developer branches were untouched.

**Requires separate disposable MySQL verification:** live unsigned/ENUM/range
and DECIMAL precision/scale behavior; VARCHAR(40) enforcement; case/accent and
other reference uniqueness behavior under utf8mb4_0900_ai_ci; DATETIME(6)
defaults/ON UPDATE; actual MySQL ROUND/CHECK/FK enforcement; Connector/Python
Decimal round trips; concurrent uniqueness and MySQL transaction/isolation
behavior. SQLite and compiled DDL checks do not establish these properties.
### BE-30 - Approved receipt mappings and read-only repository foundation

**Status as of 2026-11-20: database mappings and scoped read foundation
implemented; BE-30 remains PARTIAL. No production receiving transaction or API
has been activated. Persistent receiving remains incomplete.**

Worktree: `BranchWise_INTEGRATION`; branch: `integration/dev1-dev2`;
starting and current HEAD: `092a706`. Work remains unstaged and uncommitted.
The final original `C:/Users/bahao/Downloads/BranchWise database.sql`, receipt
DDL at lines 469-540, was inspected alongside the existing BE-29 mappings,
repository/contracts, BE-30 preparation/validation, shared repository errors,
transaction and inventory rules. The repaired DRAFT was not used. The approved
existing design is preserved; no physical application tables, migrations,
shared database changes or inventory operations were introduced.

**Mappings:** `src/branchwise/purchasing/purchase_receiving_model.py` registers
only `purchase_receipts` and `purchase_receipt_items` in existing `db.metadata`.
It imports the existing purchase-order mappings without redefining their tables
and reuses the existing identifier/timestamp type conventions.

- `purchase_receipts`: unsigned auto-increment BIGINT primary ID; required
  unsigned business/order/receiving-user IDs; required receipt_reference
  VARCHAR(40), request_key VARCHAR(120) and DATETIME(6) received_at with
  CURRENT_TIMESTAMP(6) default; nullable notes VARCHAR(500). Exact named unique
  keys preserve (business_id, id), (business_id, receipt_reference) and
  (business_id, request_key). The approved index is
  (business_id, purchase_order_id, received_at). FKs reference businesses
  directly and purchase_orders/users through composite (business_id, id) keys.
- `purchase_receipt_items`: unsigned auto-increment BIGINT primary ID; required
  unsigned business/receipt/PO-item IDs and INT UNSIGNED received_qty. Exact
  named constraints preserve received_qty > 0, unique (business_id, id), unique
  (purchase_receipt_id, purchase_order_item_id), the direct business FK, and
  composite business/receipt and business/PO-item FKs. No extra index is added.
- Both preserve approved names, nullability, InnoDB, utf8mb4,
  utf8mb4_0900_ai_ci and the absence of cascading delete/update policies.
  Importing mappings executes no connection, reflection, DDL or installation.

**Contracts and read operations:**

`purchase_receiving_contracts.py` provides the typed PurchaseReceivingRepository
port and frozen detached PurchaseReceiptRecord/PurchaseReceiptItemRecord values.
The header includes purchase_order_id, business_id and branch_id; branch_id is
loaded from the associated stored purchase order and is not a new receipt
column. These values provide safe access to the order identity and destination.

`purchase_receiving_repository.py` provides
SQLAlchemyPurchaseReceivingRepository(session_factory), implementing:

- `get_purchase_receipt(actor=..., receipt_id=...)`
- `get_purchase_receipt_item(actor=..., receipt_id=..., item_id=...)`
- `list_purchase_receipt_items(actor=..., receipt_id=...)`

Lists return an immutable tuple ordered by receipt-item ID. An accessible empty
receipt returns (); missing/inaccessible receipts and missing/inaccessible
lines use the shared RepositoryNotFoundError convention without foreign data.
Malformed IDs raise ValueError before any database session is opened.

**Security and relationship checks:** a server-resolved AuthenticatedSession is
required. Existing purchasing grants apply: Owners need purchases:manage;
Branch Managers need purchases:manage_authorized and their assigned destination
branch; Employees are denied even with forged grants. Authorization precedes
ID validation and database access. Business scope is always taken from the
trusted principal. Both queries join the receipt to its PO by business and PO
identity and enforce the trusted business and, for managers, stored PO branch.
No client-submitted business, branch, PO identity, user or grants can authorize
a receipt lookup. Session expiry/current-account checks remain in the existing
authentication layer; there is no new HTTP integration.

**Confirmed schema gap:** the separate composite FKs on receipt items ensure
business membership, but do not ensure that the referenced PO item belongs to
the parent receipt's purchase order. A same-business wrong-PO line can satisfy
all approved database constraints. The adapter therefore verifies ALL lines
of an authorized receipt before returning a header, individual line or list.
An outer join keeps unresolved/foreign-business PO-item links observable; it
never silently filters a wrong-PO line to produce a partial result. A wrong-PO
or unresolved linked item raises generic RepositoryPersistenceError, with no
partial receipt returned. Tests demonstrate the real same-business schema gap
with foreign keys enabled. No physical constraint was added or modified.

Each read owns and closes a fresh idle SQLAlchemy Session/transaction from the
injected factory. Active or pending caller units of work are rejected without
flushing, committing, rolling back or closing them. Only SELECT statements are
emitted; results escape after successful transaction exit. SQLAlchemy failures
become generic RepositoryPersistenceError with exception chaining suppressed.
There is no shared db.session wiring, creation/update/delete method, status
transition, stock movement, receipt/reference/key generator or locking claim.

**Executed verification in isolated `BranchWise_INTEGRATION_VENV`:**

- New `tests/test_purchase_receiving_repository.py`: **168 passed**. Covers
  exact mapping/DDL/default/index/FK shapes; immutable independent-session
  retrieval and stored destination; business/branch isolation, missing and
  malformed IDs, role/grant rejection before I/O; unique receipt references
  and request keys, duplicate receipt lines, positive quantity, non-null and
  business-scoped FKs; wrong-PO lines from the same/different destination;
  deliberately corrupted unresolved/foreign links in disposable fixtures;
  safe query/factory/transaction-exit errors, rollback and session closure;
  caller active/pending/dirty/deleted work preservation; SELECT-only reads and
  unchanged parent/inventory sentinel tables; unchanged preparation and lack
  of production installation. Distinct receipts may reference the same PO
  line; the adapter invents no partial/over-receiving or lifecycle policy.
- Existing BE-30 validation/service/provisional routes: **366 passed**.
- BE-29 repository/validation/service/provisional routes: **474 passed**,
  including all **135** repository regressions.
- Full backend suite: **4,190 passed** in 70.58 seconds, comprising the
  **4,022** baseline plus **168** new tests.
- Flask route count remains **49**, matching the pre-change inspection;
  production startup and route Python files are unchanged from HEAD. No
  purchasing/receiving endpoint or repository/auth installer was registered.
- `git diff --check` and whitespace checks including the four untracked Python
  files passed. Changes remain limited to those four files and this handoff.

All tests used `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, repository
`backend/pytest.ini` and explicit system-temp `--basetemp` directories:
`branchwise-be30-repository-fixed-20261120`,
`branchwise-be30-existing-20261120`, `branchwise-be30-po-regressions-20261120`
and `branchwise-be30-full-20261120`. The complete suite command was:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
..\BranchWise_INTEGRATION_VENV\Scripts\python.exe -m pytest -c backend/pytest.ini backend -q -p no:cacheprovider --basetemp "$env:TEMP\branchwise-be30-full-20261120"
```

New database integration tests use disposable SQLite files with FK enforcement,
private parent/sentinel tables and portable timestamp defaults on COPIES of the
mappings. They never create application tables in a shared/existing database.
MySQL DDL compilation is static coverage only. **Live MySQL unsigned/range and
VARCHAR length enforcement, reference/request-key uniqueness under
utf8mb4_0900_ai_ci, DATETIME(6) defaults/precision, actual MySQL CHECK/FK
execution and transaction/isolation/concurrency behavior remain UNVERIFIED.**
SQLite timestamp round trips do not verify MySQL behavior.

**Remaining BE-30 blockers:** approved orchestration against persisted PO and
line state; remaining receivable quantities and partial/over-receipt,
discrepancy/disposition/cost and lifecycle policies; receipt/reference allocation;
atomic persistence of receipt/lines, authoritative stock and append-only
movements with stable locks/revalidation; durable idempotency/replay outcomes,
payload-conflict handling and retries; disposable live MySQL verification;
production session/grant/repository wiring and an approved API contract. The
header's unique request_key alone does not implement the transaction safety
contract's payload identity, in-progress state or committed-result replay.
The schema's PO-line relationship gap remains and must also be addressed by
any future write boundary; this read guard is not a write integrity guarantee.

The existing BE-30 validation/service and provisional routes are unchanged and
remain disconnected from persistence. Structural zero-quantity acceptance
still does not authorize a receipt, and differs from the existing database
received_qty > 0 constraint. Preparation still verifies no persisted identity
or eligibility. No production receiving transaction or API has been activated.

### BE-31 - Secure internal Purchase Order Detail Service

**Status as of 2026-11-20: internal single-order detail implemented and tested;
BE-31 remains PARTIAL. Order detail does not equal Purchase History collection.
No Purchase History production endpoint is registered.**

Worktree: `BranchWise_INTEGRATION`; branch: `integration/dev1-dev2`;
starting and current HEAD: `e4300d5`. These changes remain unstaged and
uncommitted. Historical notes above describe their respective snapshots.

**Files:**

- `src/branchwise/purchasing/purchase_order_detail_service.py` adds the internal
  operation and frozen `PurchaseOrderDetail` result.
- `tests/test_purchase_order_detail_service.py` adds 78 tests, reusing BE-29's
  disposable SQLite fixtures and existing persisted mappings/read adapters.
- This handoff records the implementation, verification and remaining limits.

The keyword-only typed operation is:

```python
get_purchase_order_detail(
    *, actor: AuthenticatedSession, purchase_order_id: int,
    purchase_order_repository: PurchaseOrderRepository,
    supplier_repository: SupplierRepository | None = None,
) -> PurchaseOrderDetail
```

The immutable result contains `order: PurchaseOrderRecord`,
`items: tuple[PurchaseOrderItemRecord, ...]`, and
`supplier: SupplierRecord | None`. These are the existing detached persisted
records; no history identities, receipt timeline, spending estimates or report
fields are introduced. Stored `purchase_reference` text, status, timestamps and
Decimal amounts remain unchanged. Expected cost is not actual received
spending. No total is recalculated, status generated or reference normalized.
Empty orders remain valid and existing repository item ordering is preserved.

**Authorization and isolation:** the actor must be an explicitly trusted
AuthenticatedSession resolved by the existing SessionManager. Session expiry
and current-account/access-version checks stay in that authentication layer;
this service does not authenticate a client-supplied session-shaped value.
It reuses the BE-29 read contract through the existing role/grant/isolation
helpers: Owners require `purchases:manage`; Branch Managers require
`purchases:manage_authorized` and their assigned branch; Employees are denied.
Actor/grant checks precede ID validation and repository calls. The operation
accepts no separate client business, branch, user, role or grants.

Existing `get_purchase_order` and `list_purchase_order_items` calls receive the
same trusted actor. The BE-29 adapter derives business/branch predicates from
that actor and the stored parent order; the service also verifies the returned
header scope/identity (including a required stored branch for Owners) and every
item's business/order relationship before any result escapes. Missing,
foreign-business and unauthorized-branch orders use
the same non-disclosing RepositoryNotFoundError. Inconsistent typed records or
line relationships produce generic RepositoryPersistenceError, without partial
detail or further supplier lookup. No order or line SQL is duplicated.

**Optional supplier enrichment:** injecting a supplier repository requests
enrichment, but `suppliers:view` must separately be permitted at the verified
order scope. Without that grant, no supplier lookup/existence check occurs and
`supplier` is None; the approved supplier ID remains part of the order header.
Omitting the supplier repository also leaves `supplier` None. Authorized
enrichment uses only existing `get_supplier(actor=..., supplier_id=...)`, which
scopes by the trusted business. The returned business and supplier identity
must match the order. Missing, foreign or inconsistent requested supplier data
fails the complete detail read with a generic persistence error. Unenriched
reads rely on the approved supplier FK, as BE-29 does; they do not secretly
load restricted supplier data. No supplier activity policy is introduced.

**Session and data boundaries:** existing repositories own independent fresh
read sessions. Active/pending/dirty/deleted caller-owned units of work are
rejected without flush, commit, rollback or closure by the service. Only SELECT
statements are emitted during successful reads. Composition does not establish
a shared transaction snapshot, locking or database-enforced read-only mode.
No existing model/repository, physical table, migration, persistent data,
application factory, route or production installer was changed.

The provisional `purchase_history.py` helper is unchanged. Its synthetic
`purchase_order_reference` envelope and nonblank reference requirement differ
from approved persisted detail, so the new service returns the existing
`purchase_reference` field directly, including SQL-valid blank text.

**Executed verification in isolated `BranchWise_INTEGRATION_VENV`:**

- New order-detail suite: **78 passed** in 5.86 seconds. Covers Owner/Manager
  scope, Employee/forged-grant denial before access, malformed IDs, concealed
  missing/inaccessible orders, zero/multiple lines, poisoned adapter records,
  separate supplier grants, foreign/missing supplier relationships, preserved
  references/statuses/Decimal context, immutable detached results, independent
  closed sessions, SELECT-only reads, unchanged table snapshots, later read
  failures, caller-owned work preservation and blocked network operations.
- Existing targeted regressions: **682 passed** in 20.38 seconds: all **132**
  BE-31 tests; **135** BE-29 repository tests; **168** BE-30 repository tests;
  **53** supplier repository tests; **115** permissions, **20** isolation,
  **42** session-auth and **17** shared repository-contract tests.
- Full backend suite on final code: **4,268 passed** in 60.13 seconds, comprising
  the **4,190** baseline plus **78** new tests.
- Flask route map: **49 rules**, exactly unchanged before/after importing the
  service; no purchasing endpoint. Production startup/routes are unchanged.
- `git diff --check` and separate new-file whitespace/syntax checks passed.
  `git status -sb` shows only this modified handoff and the two new Python files;
  nothing is staged or committed. HEAD remains `e4300d5`.

Tests used `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, repository
`backend/pytest.ini` and unique explicit system-temp basetemp directories named
`branchwise-be31-detail-*`, `branchwise-be31-detail-regressions-*` and
`branchwise-be31-detail-full-*`. The full-suite command was:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$be31Temp = Join-Path ([System.IO.Path]::GetTempPath()) ('branchwise-be31-detail-full-' + [guid]::NewGuid().ToString('N'))
..\BranchWise_INTEGRATION_VENV\Scripts\python.exe -m pytest -c backend/pytest.ini backend -q -p no:cacheprovider --basetemp $be31Temp
```

All database checks used disposable SQLite files with copied test metadata and
FK enforcement; deliberately broken supplier links were confined to those
fixtures. No shared database was accessed or altered. Live MySQL Decimal
driver behavior, unsigned/range/ENUM enforcement, collation, DATETIME(6), actual
CHECK/FK execution, transaction isolation/concurrency and database-enforced
read-only guarantees remain **UNVERIFIED**. SQLite and existing static MySQL DDL
compilation do not establish those properties.

**Remaining BE-31 blockers:** general Purchase History read permissions,
collection granularity/identities, ordering/tie-breakers, pagination/limits,
status/date/filter semantics, response fields, supplier historical snapshots,
source completeness/snapshot guarantees and production security/repository/API
wiring remain unapproved or incomplete. Existing purchasing read authorization
is reused only for this single-order service; no new general BE-31 history-view
grant is inferred. Receipt costs and received-spending calculations are absent.

## BE-34 internal stock-transfer read adapter

The internal SQLAlchemy mappings in `src/branchwise/transfers/stock_transfer_model.py`
map only the `stock_transfers` and `stock_transfer_items` tables from
`schema/BranchWise_database_repaired_DRAFT.sql`, using shared `db.metadata`.
Importing the mappings does not execute DDL, migrations, or database access.
The draft schema has not been executed on MySQL 8.

`SQLAlchemyStockTransferRepository` provides SELECT-only operations to retrieve
one transfer, retrieve one item, and list a transfer's items. It derives
business and branch scope from a trusted `AuthenticatedSession`, checks both
branch records against the transfer business, validates the item-parent
relationship, and returns frozen detached records. Owner reads are limited to
the owner's business. Branch Managers must hold the operation grant at the
relevant side: source for approval/dispatch, destination for receipt. Employees
are denied even if a forged grant is present.

There is no general transfer-history view permission. The internal repository
requires an explicit approval, dispatch, or receipt purpose; request grants are
not accepted as a read purpose. No route, production API, or app extension
installs this adapter. This does not implement transfer creation, approval,
dispatch, receiving, cancellation, or inventory mutation.

The focused SQLite repository suite passed (12 tests); the selected BE-34–37,
Developer 1 inventory, authorization/isolation, transaction-safety, session,
startup, and route-map regressions passed (736 tests). The full backend suite
passed: 4,280 tests. The route-map test still reports 49 routes; this adapter
adds no route or app extension. MySQL DDL compilation and disposable SQLite
constraints are not live MySQL verification. MySQL 8 ENUM/unsigned enforcement,
CHECK/FK execution, collation, DATETIME(6), driver behavior, and
transaction/read-only guarantees remain **UNVERIFIED**.

## BE-35 secure transfer decision preparation

`src/branchwise/transfers/transfer_decision_service.py` exposes the internal
`prepare_transfer_decision(*, actor, decision_input, repository)` operation.
It authorizes a trusted Owner or Branch Manager for
`transfers:approve_source` before calling the existing decision-envelope
validator or the BE-34 repository. The repository read uses
`TransferReadPurpose.APPROVE_SOURCE`, which scopes a Manager to the transfer's
source branch. Foreign-business, missing, and out-of-scope transfers remain
non-disclosing not-found results.

For a PENDING transfer, rejection is checked with Developer 1's
`validate_status_transition(PENDING, REJECTED)` rule and returned as a typed
`PREPARED_NOT_PERSISTED` result with `expected_current_status=PENDING` and
`target_status=REJECTED`. It is not a persisted rejection or a reservation;
any future write must re-read and compare the current state inside its own
transaction. Rejected, cancelled, completed, and IN_TRANSIT transfers are
blocked from repeated rejection. Approval is explicitly blocked because the
schema has no APPROVED status and the approval-to-dispatch policy is unresolved;
no target status is invented and approval never dispatches stock here.
Persistent atomic decision updates, concurrency/idempotency handling, and
approval/dispatch policy confirmation remain pending.

The service performs no writes to transfer, balance, or movement tables and is
not registered as a route or application extension. The focused service suite
passed (23 tests); the selected BE-34, BE-35, Developer 1 transfer/security,
session, transaction, and startup regressions passed (758 tests). The full
backend suite passed: 4,303 tests. The route map remains at 49 routes with no
transfer route. MySQL-specific behavior remains **UNVERIFIED**.

## BE-36 secure transfer receiving preparation

`src/branchwise/transfers/transfer_receiving_service.py` exposes the internal
`prepare_transfer_receiving(*, actor, receiving_input, repository)` operation.
It requires a trusted Owner or Branch Manager with
`transfers:confirm_receipt` before input validation or protected reads. The
BE-34 adapter is called with `TransferReadPurpose.CONFIRM_RECEIPT`; Owners are
limited to their business and Branch Managers to the destination branch.
Employees, forged sessions/grants, foreign-business records, and inaccessible
transfers are denied without exposing transfer existence.

Only persisted `IN_TRANSIT` transfers are eligible for preparation. PENDING is
blocked as not dispatched; COMPLETED, REJECTED, and CANCELLED are not receivable.
The service validates the full persisted line set, business/parent relations,
quantity conservation, and transfer variants, then reuses Developer 1's
`calculate_receipt_quantities` and `calculate_quantity_discrepancies` helpers.
It prepares only a complete receipt of current in-transit quantities. Partial
receiving, unresolved discrepancies, and returned/written-off quantity
resolution are blocked pending approved policy. The result is immutable and
explicitly not persisted, does not reserve stock, and requires disposition
before any inventory effect.

This is not a receipt confirmation or concurrency reservation. The service
does not write transfer/item, branch-inventory, or inventory-movement records
and is not registered as a production route or app extension. Persistent
receiving, discrepancy settlement, disposition, atomic status/quantity updates,
idempotency, and MySQL concurrency behavior remain pending. The draft schema
and SQLite tests do not establish MySQL behavior; MySQL 8 ENUM/unsigned,
CHECK/FK, collation, DATETIME(6), driver, locking, and transaction guarantees
remain **UNVERIFIED**.

The focused BE-36 service tests passed (30 tests); the selected BE-34–37,
Developer 1 transfer/inventory, authorization/isolation, transaction, session,
security, startup, and route regressions passed (828 tests). The full backend
suite passed: 4,333 tests. The Flask route map remains at 49 routes and has no
transfer route.
