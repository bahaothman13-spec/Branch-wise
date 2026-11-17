# BE-57 Postman collection

Import `BranchWise.postman_collection.json` as Collection v2.1. Start the
existing app from the repository root with
`.\.venv\Scripts\python.exe -B -m flask --app backend.src.app run`.
The collection variable `base_url` defaults to `http://127.0.0.1:5000`.
Run the Registered API folder to check GET `/api/health` (200, status ok).
No credentials or authentication contract are supplied.

The separate NOT REGISTERED / NOT IMPLEMENTED folder documents only the
three existing provisional POST Blueprints. Its inherited prerequest script
uses `pm.execution.skipRequest()` to disable requests in supported Postman
runtimes. Use a runtime supporting this API; do not rely on older clients to
skip requests. Requests can still be inspected without sending them.

These paths return 404 in the current main app. Only an isolated test app
with the existing Blueprints registered produces the documented 400/501
behavior. Do not register them in production to use this collection. To
experiment against an isolated server, explicitly remove the folder guard
in your imported copy. No isolated server is deployed by this deliverable.

Valid and invalid JSON examples reproduce existing validator/route-test
cases. Structurally valid inputs return 501 with a pending-integration
message; they do not create records or change stock. Invalid fields return
400 with `errors`; malformed/non-object JSON or non-JSON content returns
400 with `error`. Decimal purchase costs use strings: JSON floats fail
validation. Example integer IDs are synthetic and imply no database records.

Remaining integration gaps (no HTTP requests are invented here):

| Tasks | Required integration |
| --- | --- |
| BE-31 | Purchase history/supplier record routes, scoped repositories and response contracts |
| BE-34–37 | Transfer request, approval/dispatch, receipt/discrepancy and cancellation routes; approved lifecycle, stock ledger, atomic transactions and durable idempotency |
| BE-38 | Trusted AI service configuration, provider integration and approved route contracts |
| BE-39–42 | Historical sales, forecast, replenishment and alternative comparison routes with authorized real inputs and approved business rules |
| BE-43–45 | Business questions, decision logs and reliability/fallback integration; scoped context, persistence and response contracts |
| BE-46–50 | Dashboard, sales/product, inventory/purchase, branch comparison and export routes; approved report definitions, source completeness, filters and export contracts |

All require coordination with Developer 1 for trusted authentication,
authorization and approved database contracts. Existing Python helpers are
not deployed APIs. The handoff remains unchanged; this collection completes
the independent BE-57 documentation artifact, not those integration tasks.
