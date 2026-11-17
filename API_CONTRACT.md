# BranchWise API Contract

This document describes the Flask API that currently exists in `backend/routes`
and conventions for extending it. It follows the approved stories and
`backend/ROLE_PERMISSIONS.md`. It does not imply that unimplemented business
operations or database integration are complete.

## Base URL and payloads

- API routes use the `/api` prefix.
- JSON request bodies must be JSON objects where a body is required. Field names
  use `snake_case` (for example, `owner_name`, `branch_id`, and `is_active`).
- Identifiers in current paths are positive decimal integers.
- Requests that send a body should use `Content-Type: application/json`.
- Responses from these routes use `application/json`.
- The default maximum request body size is 1 MiB. Deployments may configure a
  smaller or larger positive limit up to 16 MiB. A request above the configured
  limit returns `413` with `{"error":"Request body is too large."}`.

For routes that require a JSON body, after any required session and CSRF checks,
a missing or non-JSON content type returns
`400` with `{"error":"Request body must be JSON."}`. An empty or malformed
JSON body, or a valid JSON value that is not an object, returns `400` with
`{"error":"Request body must be a JSON object."}`. Current email fields are
trimmed and checked for a local part and dot-separated domain labels; invalid
values return a field-level validation error.

## Response structures

The shared response helpers in `backend/response_helpers.py` centralize JSON
serialization and status codes. The success helper passes its payload through
unchanged to preserve current endpoint contracts. The error helper supports
both current validation bodies and simple error bodies.

Existing endpoints intentionally retain their established response shapes:

```json
{"status": "ok"}
```

```json
{"message": "Database authentication is not implemented yet."}
```

```json
{"error": "Request body must be JSON."}
```

```json
{"errors": {"email": "Enter a valid email address."}}
```

For future endpoints, use a success payload with a `data` object and an
optional `message`, for example `{"data": {"id": 12}, "message": "Created."}`.
Use `error` for a general error and `errors` for field-level validation
messages. Do not include submitted passwords or other secrets in error details.
Validation messages should be short and keyed by the request field; they should
not echo the rejected value. Keep old endpoint shapes stable unless their
contracts are intentionally versioned or changed as part of an approved task.
JSON endpoints reject fields outside their documented input allowlist with a
`400` validation error under `errors.body`. The response does not reflect
unknown key names or values. This prevents future persistence code from
accidentally treating client-supplied role, owner, status, or other internal
fields as writable.

## Security foundations and current limits

Responses include `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, a restrictive `Content-Security-Policy`, and
`Permissions-Policy`. These headers do not alter the current JSON payloads.
The API does not enable CORS and does not return `Access-Control-Allow-Origin`;
credentialed cross-origin browser requests are not supported. Do not add
wildcard CORS when session cookies are integrated. Configure an exact trusted
origin list only if a future deployment requires cross-origin access.

Generic JSON error handling hides exception details. Unexpected request
exceptions are not written to application logs with exception text or a
stack trace. Database connection failures also use generic log messages.
HSTS is not set by the application; the production TLS-terminating ingress
must enforce HTTPS and may set HSTS once HTTPS is configured.

The current app factory validates security configuration. Production must set
`APP_ENV="production"`, `SESSION_COOKIE_SECURE=True`, `FLASK_DEBUG=False` (or
`DEBUG=False` in app config), and a random `SECRET_KEY` containing at least 32
bytes. These settings can be read from the process environment or
`backend/.env`; explicit factory config takes precedence. The login and
registration endpoints accept passwords up to 1,024
characters and return the existing field-level `400` response shape when that
limit is exceeded. Production startup also rejects the default in-memory
SQLite database URI; a durable database configuration is required.
`MAX_CONTENT_LENGTH` defaults to 1 MiB. Login limit defaults are 5 attempts per
normalized identifier and 30 per client address in 15 minutes, but the prepared
rules run on `/api/auth/login` only when an atomic, shared `RateLimitStore` and
a key secret of at least 32 bytes are explicitly installed. A missing or failed
limiter fails closed; the default app does not install one.

The authentication routes now orchestrate registration, credential checking,
session issuance, and logout when explicitly supplied with an `AuthRepository`,
`SessionManager` backed by a persistent `SessionStore`, and the login limiter.
`create_app()` installs none of these adapters, so the default routes retain
their explicit `501` responses. No production repository, session store, or
rate-limit store is configured. Session-backed authentication and route-level
authorization are active for the account and business-settings routes. Branch
and owner-managed user-management routes still await their own session-backed
authorization and persistence.
The existing CSRF helper in `session_auth.py` is reused for logout.

Session invalidation uses the approved version names: the account repository
supplies `users.access_version`, each session snapshots it as
`access_version_at_login`, and the persistent adapter maps that snapshot to
`auth_sessions.access_version_at_login`. A mismatch against the freshly loaded
user deletes and rejects the session. This is application behavior only; it
does not create or alter database schema.

The Postman collection is `backend/postman/BranchWise-Authentication.postman_collection.json`;
the companion local environment is `backend/postman/BranchWise-Local.postman_environment.json`.
The variables provide `baseUrl`, `owner_name`, `business_name`, `email`,
`password`, and `csrf_token`; the Login request captures the readable CSRF
cookie for Logout, while Postman's cookie jar retains both auth cookies. Saved
response examples document successful and common failure responses.
Successful registration, login, and logout require persistent MySQL-backed
adapters; the default app deliberately returns `501` when the required
adapters are absent. Pytest fakes are local to tests and cannot be used by
Postman.

## HTTP status codes

| Status | Meaning and use |
| --- | --- |
| `200` | Request completed successfully; used by `/api/health`, account/settings routes, and product, category, and variant list/update/status routes. |
| `201` | A resource was created successfully. Owner registration and product, category, or variant creation return this only when their repository completes the required atomic write. |
| `400` | Malformed request, invalid JSON shape, or current field/path validation failure. Existing validation endpoints use this status for compatibility. |
| `401` | Authentication is missing or invalid. Login returns this for invalid credentials; logout and account/business-settings routes require a valid session. |
| `403` | An authenticated user lacks permission, a password check fails, or a session-backed state change fails CSRF validation. Account changes and logout require a valid CSRF token. |
| `404` | The route or resource was not found. Unknown routes return a generic JSON error. When resource scoping is implemented, use `404` for inaccessible cross-business resources if revealing their existence would leak information. |
| `409` | A request conflicts with existing state, including duplicate email during owner registration. |
| `429` | Login attempts exceeded an installed account or client rate limit. |
| `413` | Request body exceeds the configured maximum size; returns a generic JSON error. |
| `422` | A syntactically valid request fails semantic validation. Existing validation uses `400`; retain that behavior unless a contract change is approved. |
| `500` | Unexpected server failure. The response is generic and must not include an exception, stack trace, SQL, credentials, or password. |
| `501` | The endpoint is recognized but its operation is unavailable. Account, business-settings, product, category, and variant routes return this when their required persistence repository is not installed. |
| `503` | Login rate-limit persistence is unavailable; authentication fails closed. |

Validation errors are returned as a field-to-message object under `errors`.
Authorization failures should use `401` for missing/invalid identity and `403`
for a verified identity without the required role, permission, or business or
branch scope. A missing resource uses `404`. Error text must not expose whether
another business owns a resource when that would reveal private information.

## Dates and times

The current routes do not return date or timestamp fields. When a route adds
them, use ISO 8601: date-only values as `YYYY-MM-DD`; timestamps as UTC with a
`Z` suffix, for example `2026-11-06T14:30:00Z`. Do not return locale-dependent
date strings.

## Pagination and filtering

Product, category, and variant list routes use query parameters `page`
(1-based, default `1`) and `per_page` (default `20`, maximum `100`). Return the
requested values and totals alongside `data`, for example:

```json
{
  "data": [],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 0,
    "total_pages": 0
  }
}
```

List filters use documented, resource-specific `snake_case` query parameters,
and only explicitly supported filters affect results. Invalid page numbers,
page sizes, and filter values return the validation error convention.

## Account and business settings

These routes use the session created by `/api/auth/login`. Reads require a
valid session. State-changing requests require the matching CSRF cookie and
`X-CSRF-Token` header. Account identity, role, business scope, and branch scope
come only from the verified session and server-loaded repository records.

`GET /api/users/me` returns only `user_id`, `name`, `email`, `role`,
`business_id`, `branch_id`, and `is_active`. It never returns a password hash
or access-version data. `PATCH /api/users/me` accepts a non-empty subset of
`name` and `email`; users cannot update their role, branch, active state, or
business ID through this route. Names and emails are trimmed, and email values
use the shared email validator.

`PATCH /api/users/me/password` accepts `current_password`, `new_password`,
and `confirm_password`. The current password must verify; the new password must
be 8–1,024 characters and match its confirmation. The route hashes the new
password before calling the repository and never passes plaintext passwords
to persistence. The repository operation must atomically compare the current
session's access version, update the password hash, increment the version,
write the account audit event, and revoke the user's sessions.

`GET /api/business/settings` returns the Owner's `business_id`,
`business_name`, and existing contact `email`. `PATCH /api/business/settings`
accepts a non-empty subset of `business_name` and `email`. Only an Owner with
the `business_settings:manage` grant may use these endpoints. The supported
contact field is the existing business email; extra fields such as phone,
role, branch, or business ID are rejected.

Successful reads return `200` with `{"data":{...}}`. Profile and business
settings updates return `200` with the updated `data` and a short `message`;
password changes return `200` with a success `message` only.
Missing or malformed JSON and invalid fields return `400`; missing sessions
return `401`; CSRF failures and denied Owner access return `403`; a repository
conflict returns `409`; missing scoped data returns `404`; persistence failures
return a generic `500`. If the authenticated route has no required repository,
it returns `501` and does not report a successful change. The default
`create_app()` installs no account or business-settings repositories.

Example account response:

```json
{
  "data": {
    "user_id": 23,
    "name": "Test Owner",
    "email": "owner@example.com",
    "role": "Owner",
    "business_id": 10,
    "branch_id": null,
    "is_active": true
  }
}
```

Example account profile update request:

```json
{"name":"Updated Owner","email":"owner@example.com"}
```

Example password update request:

```json
{
  "current_password": "<current password>",
  "new_password": "<new password>",
  "confirm_password": "<new password>"
}
```

Successful password update response:

```json
{"message":"Password changed successfully."}
```

Example business update request and response:

```json
{"business_name":"BranchWise Market","email":"contact@example.com"}
```

```json
{
  "data": {
    "business_id": 10,
    "business_name": "BranchWise Market",
    "email": "contact@example.com"
  },
  "message": "Business settings updated."
}
```

## Product management (BE-15)

The product catalog is shared across one business and is not branch-scoped.
Every product query is scoped by the authenticated session's business ID;
request fields cannot select a business. Owners with `product_catalog:manage`
can create, edit, activate, and deactivate products. Owners, Branch Managers,
and Employees with `products:view` can list and search their business catalog.
Product writes require a valid session and matching CSRF cookie/header.
Branch Managers and Employees cannot write directly to the shared catalog;
employee proposals are handled by the separate product approval APIs (BE-21)
and are never applied directly by these endpoints.

`POST /api/products` accepts `name` (required string, at most 180 characters),
`product_code` (required string, at most 80 characters), `category_id` (required
positive integer), `selling_price` (required exact decimal string or integer),
and optional `cost_price` (same decimal format, defaults to `0.000`). Prices
must be finite, non-negative, fit `DECIMAL(14,3)`, and have at most three
fractional digits. Names and codes are trimmed. A code must be unique,
case-insensitively, across the same business's product and variant codes. The
repository creates the product and its schema-required default variant in one
atomic operation and rechecks uniqueness within the transaction; the endpoint
does not manage variant fields or stock.

`PATCH /api/products/{product_id}` accepts a non-empty subset of the same
metadata fields. Omitted values remain unchanged. A category must exist in the
same business and be active when assigning or changing to it. Existing product
references and history remain intact.

`PATCH /api/products/{product_id}/status` accepts only `{"is_active": boolean}`.
It supports activation and deactivation using the existing product status
rules. Deactivation preserves product, variant, stock, and transaction history;
products cannot be permanently deleted.

`GET /api/products` accepts `q` (case-insensitive name or product-code search,
up to 180 characters), `category_id` (positive integer), `is_active` (`true` or
`false`), `page` (1-based, default `1`, maximum `1000000`), and `per_page`
(default `20`, maximum `100`). Each filter may appear once. Results are filtered
within the authenticated business before pagination. The response uses the
standard `data` and `pagination` envelope; each product includes only
`product_id`, `name`, `product_code`, `category_id`, `selling_price`,
`cost_price`, and `is_active`. Price fields are returned as fixed three-place
decimal strings. Product detail, variants/categories, and branch stock are
owned by BE-16/BE-17 and are not included here.

Successful list and update/status operations return `200`; product creation
returns `201`. Invalid JSON, fields, filters, path IDs, or business-rule values
return `400` with the existing `errors` object. Missing sessions return `401`,
failed CSRF or denied permissions return `403`, missing or cross-business
products/categories return `404`, duplicate catalog codes and repository
conflicts return `409`, and persistence failures return a generic `500`. If
the ProductRepository is not installed, authorized valid operations return
`501` and make no claim that data was saved. The default `create_app()` installs
no ProductRepository.

Example creation request:

```json
{
  "name": "Green Tea",
  "product_code": "TEA-01",
  "category_id": 7,
  "selling_price": "2.500",
  "cost_price": "1.250"
}
```

Example list response:

```json
{
  "data": [
    {
      "product_id": 31,
      "name": "Green Tea",
      "product_code": "TEA-01",
      "category_id": 7,
      "selling_price": "2.500",
      "cost_price": "1.250",
      "is_active": true
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 1,
    "total_pages": 1
  }
}
```

## Product categories and variants (BE-16)

Categories and variants are shared across their business, not branch-scoped.
Every read and write uses the business ID from the authenticated session;
clients cannot select or reassign a business. Reads require `products:view`.
Direct catalog writes require an Owner with `product_catalog:manage`, a valid
session, and the matching CSRF cookie and `X-CSRF-Token` header. Branch
Managers and Employees may read their business catalog but cannot directly
write categories or variants. Employee proposals use the separate BE-21
approval workflow; these endpoints never apply Employee changes directly.

`GET /api/categories` accepts `is_active` (`true` or `false`), `page`, and
`per_page`. Each query parameter may appear only once. It returns categories
in the authenticated business with `category_id`, `name`, and `is_active`.
`POST /api/categories` accepts only `name` (required string, at most 120
characters after trimming); normalized names must be unique case-insensitively
within the business. `PATCH /api/categories/{category_id}` accepts a non-empty
subset of `name`. `PATCH /api/categories/{category_id}/status` accepts only
`is_active` (boolean). Categories are deactivated rather than deleted, so
product references remain valid. An inactive category cannot be assigned to a
product.

`GET /api/products/{product_id}/variants` accepts `is_active`, `page`, and
`per_page` and returns variants belonging to that same-business product. Each
variant contains `variant_id`, `product_id`, `sku`, `size`, `color`, optional
`selling_price` and `cost_price` overrides, `is_default`, and `is_active`.
Null price overrides inherit the product's corresponding price. Inventory and
stock quantities are not part of this response.

`POST /api/products/{product_id}/variants` accepts `sku` (required string, at
most 80 characters), optional `size` and `color` strings, optional exact
decimal `selling_price` and `cost_price` overrides, and optional
`is_default` (boolean, default `false`). A non-default variant requires at
least one size or color; a default variant cannot have either attribute. A
product can have at most one default variant. Product creation in BE-15 already
creates its required default variant atomically, so ordinary option variants
are created with `is_default:false`. The SKU is checked case-insensitively
within the same business and against the existing product/variant catalog code
registry. The repository must repeat the uniqueness and default-variant checks
inside the create transaction.

`PATCH /api/products/{product_id}/variants/{variant_id}` accepts a non-empty
subset of `sku`, `size`, `color`, `selling_price`, `cost_price`, and
`is_default`; omitted fields remain unchanged and explicit null clears an
optional size, color, or price override where the variant rules allow it. SKU
uniqueness and default-variant constraints are rechecked for the same business
and product. The product association cannot be changed. The repository updates
the SKU registry atomically with variant metadata.
`PATCH /api/products/{product_id}/variants/{variant_id}/status` accepts only
`is_active` (boolean). Deactivation preserves historical inventory and
transaction references. Neither category nor variant APIs support deletion.

List responses use the standard `data` and `pagination` envelope. Create
operations return `201`; lists and updates return `200`. Invalid JSON, fields,
IDs, filters, or variant rules return `400`; missing sessions return `401`;
failed CSRF, denied permissions, or an attempted direct Employee write return
`403`; missing or cross-business products, categories, or variants return
`404`; duplicate names/SKUs and repository conflicts return `409`; persistence
failures return a generic `500`. Without the installed
`CategoryVariantRepository`, authorized valid operations return `501` and do
not report success. `create_app()` installs no catalog repository. Installing a
`CategoryVariantRepository` also installs that adapter for BE-15 product APIs,
so product creation, variants, and the shared code registry use one transaction
boundary and one source of tenant-scoped catalog data.

Example category creation request and response:

```json
{"name":"Beverages"}
```

```json
{
  "data": {
    "category_id": 7,
    "name": "Beverages",
    "is_active": true
  },
  "message": "Category created."
}
```

Example variant creation request and response:

```json
{
  "sku": "TEA-M-RED",
  "size": "M",
  "color": "Red",
  "selling_price": "2.750",
  "cost_price": "1.125"
}
```

```json
{
  "data": {
    "variant_id": 902,
    "product_id": 31,
    "sku": "TEA-M-RED",
    "size": "M",
    "color": "Red",
    "selling_price": "2.750",
    "cost_price": "1.125",
    "is_default": false,
    "is_active": true
  },
  "message": "Variant created."
}
```

Example variant update and status requests:

```json
{"sku":"TEA-M-BLUE","color":"Blue","selling_price":"3.000"}
```

```json
{"is_active":false}
```

## Current routes

| Method and path | Current behavior |
| --- | --- |
| `GET /api/health` | Returns `200` with `{"status":"ok"}`. |
| `POST /api/auth/register` | Accepts `owner_name`, `business_name`, `email`, and `password`; validates input (`400`); when an `AuthRepository` is installed, atomically creates the Owner and business without a branch (`201`), or returns `409` for a duplicate email. Repository failures return a generic `500`. Without a repository it retains `501`. |
| `POST /api/auth/login` | Accepts `email` and `password`; validates input (`400`); requires installed account, session, and rate-limit adapters. Invalid or inactive credentials return `401`, rate limiting returns `429`, adapter failures return generic `500`/`503`, and success sets session and CSRF cookies and reports whether first-branch onboarding is required. Missing adapters retain `501`. |
| `POST /api/auth/logout` | Requires an installed session manager, valid session, and matching CSRF cookie/header; revokes the session and clears both cookies. Persistence failures return a generic `500`. Missing session management retains `501`; unauthenticated requests return `401`, and failed CSRF validation returns `403`. |
| `GET /api/branches` | Requires an authenticated Owner with `branches:manage`; lists only the Owner's business branches. Returns `501` without BranchRepository. |
| `POST /api/branches` | Requires an authenticated Owner and CSRF; creates a branch from validated `name`, `address`, `phone`, and `notes`. Returns `201` after atomic save, `400` invalid data, `403` denied, `409` duplicate name, `501` without BranchRepository. |
| `GET /api/branches/{branch_id}` | Requires Owner authorization and returns a same-business branch; invalid ID `400`, out of scope `404`, missing repository `501`. |
| `PATCH /api/branches/{branch_id}` | Requires Owner and CSRF; accepts a non-empty subset of `name`, `address`, `phone`, and `notes`; preserves history. Returns `200` on save, `400` invalid, `404` outside scope, `409` duplicate name, `501` without repository. |
| `PATCH /api/branches/{branch_id}/deactivate` | Requires Owner and CSRF; soft-deactivates the branch and preserves history. Returns `200` on save, `404` outside scope, `501` without repository. |
| `GET /api/users` | Requires Owner with `users:manage`; lists same-business staff without password data. Returns `501` without UserRepository. |
| `POST /api/users` | Requires Owner and CSRF; accepts `name`, `email`, `role` (`Branch Manager` or `Employee`), `initial_password`, and an active same-business `branch_id`. Hashes the password before the repository call. Returns `201`, `400` invalid, `403` denied, `404` invalid branch, `409` duplicate email, `501` without UserRepository and BranchRepository. |
| `PATCH /api/users/{user_id}` | Requires Owner and CSRF; changes staff `role` and `branch_id` atomically. Returns `200`, `400` invalid, `404` outside scope, `409` inactive branch, `501` without repositories. |
| `PATCH /api/users/{user_id}/status` | Requires Owner and CSRF; accepts `is_active`, increments access version and revokes sessions through the repository. Returns `200` after save, `400` invalid, `404` outside scope, `501` without UserRepository. |
| `GET /api/users/me` | Requires a valid session; returns safe fields for the authenticated account. Returns `501` if the user repository is absent, `403` if the trusted account context is invalid, and `500` for invalid persisted account data. |
| `PATCH /api/users/me` | Requires a valid session and CSRF token; accepts a non-empty subset of `name` and `email`. Returns `400` for invalid fields, `409` for email conflict, `501` without the user repository, and `200` only after the repository completes the update. |
| `PATCH /api/users/me/password` | Requires a valid session and CSRF token; verifies the current password and validates the new password (`400` for invalid input, `403` for an incorrect current password). Returns `501` without the user repository and `200` only after the repository atomically updates the hash and invalidates prior sessions. |
| `GET /api/business/settings` | Requires a valid Owner session with `business_settings:manage`; returns the business name and contact email. Returns `403` for other roles, `501` without the settings repository, and `200` only when settings are loaded. |
| `PATCH /api/business/settings` | Requires a valid Owner session and CSRF token; accepts a non-empty subset of `business_name` and `email`. Returns `400` for invalid fields, `403` for non-Owners, `409` for conflicts, `501` without the settings repository, and `200` only after persistence succeeds. |
| `GET /api/products` | Requires a valid session and `products:view`; supports `q`, `category_id`, `is_active`, `page`, and `per_page`. Returns same-business catalog rows and pagination; `400` for invalid filters, `401` unauthenticated, `403` denied, `501` without ProductRepository. |
| `POST /api/products` | Requires an Owner with `product_catalog:manage`, valid session, and CSRF; validates product metadata, active same-business category, and shared product/variant code uniqueness. Returns `201` only after atomic repository creation, `400` invalid input, `403` denied, `404` missing category, `409` duplicate code, `501` without ProductRepository. |
| `PATCH /api/products/{product_id}` | Requires an Owner with `product_catalog:manage`, valid session, and CSRF; accepts a non-empty metadata subset. Returns `200` after scoped update; `400` invalid data, `403` denied, `404` missing/cross-business product or category, `409` duplicate code, `501` without ProductRepository. |
| `PATCH /api/products/{product_id}/status` | Requires an Owner with `product_catalog:manage`, valid session, and CSRF; accepts `is_active` boolean and preserves history on deactivation. Returns `200` after persistence, `400` invalid ID/body, `403` denied, `404` missing/cross-business product, `501` without ProductRepository. |
| `GET /api/categories` | Requires a valid session and `products:view`; supports `is_active`, `page`, and `per_page`; returns same-business category rows. Returns `400` for invalid filters, `401` unauthenticated, `403` denied, and `501` without CategoryVariantRepository. |
| `POST /api/categories` | Requires Owner `product_catalog:manage`, valid session, and CSRF; accepts `name` up to 120 characters. Returns `201` after scoped persistence, `400` invalid body, `403` denied, `409` duplicate name, `501` without CategoryVariantRepository. |
| `PATCH /api/categories/{category_id}` | Requires Owner `product_catalog:manage`, session, and CSRF; accepts `name`. Returns `200` after scoped update, `400` invalid input, `403` denied, `404` missing/cross-business category, `409` duplicate name, `501` without CategoryVariantRepository. |
| `PATCH /api/categories/{category_id}/status` | Requires Owner `product_catalog:manage`, session, and CSRF; accepts `is_active` and retains product references. Returns `200` after persistence, `400` invalid ID/body, `403` denied, `404` missing/cross-business category, `501` without CategoryVariantRepository. |
| `GET /api/products/{product_id}/variants` | Requires session and `products:view`; supports `is_active`, `page`, and `per_page`; returns only same-business variants for the product. Returns `400` invalid filters/ID, `401` unauthenticated, `403` denied, `404` missing/cross-business product, `501` without CategoryVariantRepository. |
| `POST /api/products/{product_id}/variants` | Requires Owner `product_catalog:manage`, session, and CSRF; accepts SKU, size/color, price overrides, and default flag. Returns `201` only after atomic variant and SKU-registry creation; `400` invalid rules/body, `403` denied, `404` missing/cross-business product, `409` duplicate SKU/default conflict, `501` without CategoryVariantRepository. |
| `PATCH /api/products/{product_id}/variants/{variant_id}` | Requires Owner `product_catalog:manage`, session, and CSRF; accepts a non-empty subset of SKU, attributes, price overrides, and default flag. Returns `200` only after atomic scoped update; `400` invalid input, `403` denied, `404` missing/cross-business resource, `409` duplicate SKU/default conflict, `501` without CategoryVariantRepository. |
| `PATCH /api/products/{product_id}/variants/{variant_id}/status` | Requires Owner `product_catalog:manage`, session, and CSRF; accepts only `is_active`, preserving history. Returns `200` after persistence, `400` invalid ID/body, `403` denied, `404` missing/cross-business resource, `501` without CategoryVariantRepository. |

## Operational APIs (BE-17 through BE-27, BE-32/33)

Operational writes require a valid server-side session and matching CSRF cookie
and `X-CSRF-Token` header. Branch and business IDs are authorization selectors
only; each repository loader must resolve them inside the authenticated
business, enforce active branch/product/variant state, and return typed trusted
contexts. Employees and Branch Managers are restricted to their assigned
branch. Owner reads may select branches within the Owner's business. Branch
dependent operations are unavailable until first-branch onboarding is complete.
The app has no default OperationsRepository, so authorized valid operations
requiring its data return `501` without reporting success.

`GET /api/products/{product_id}/overview` supports optional `branch_id`,
`movement_since` (ISO-8601 timestamp), and `movement_limit` (1–100, default 10).
It returns category and product details, variants, available/inspection/damaged/
in-transit quantities by permitted branch, minimum-stock levels, Low/Out status,
and recent movements. Missing balances are zero. Employees cannot see cost
prices or responsible-user details unless their grants allow it. Requires
`products:view`; malformed filters return `400`, foreign resources `404`,
denied access `403`, and missing OperationsRepository `501`.

`POST /api/branches/{branch_id}/inventory/adjustments` accepts
`product_variant_id`, `direction` (`INCREASE` or `DECREASE`), positive integer
`quantity`, and non-empty `reason`. It also requires `Idempotency-Key`. The
route validates branch/product/variant scopes and current available quantity;
decreases cannot produce negative stock. The repository must append an
immutable ledger movement and its business/operation-scoped idempotency record
atomically. Returns `201` only after commit, `400` invalid quantity/reason/key,
`403` denied, `404` missing/cross-business resources, `409` duplicate key with a
different payload or state conflict, `501` without OperationsRepository.

`POST /api/product-requests` accepts `change_type` (`Add Product`,
`Modify Product`, `Add Variant`, or `Modify Variant`), a non-empty `reason`,
and `payload` object. It creates a pending immutable snapshot and never changes
the active catalog. Employees can submit; Branch Managers can comment with
`PATCH /api/product-requests/{request_id}/comment` and `{"comment":"..."}`;
only Owners can approve with `POST /api/product-requests/{request_id}/approve`
or reject with `POST /api/product-requests/{request_id}/reject` and
`{"reason":"..."}`. `GET /api/product-requests` lists only the caller's
authorized requests and accepts optional `status` (`Pending`, `Approved`,
`Rejected`). Approval must revalidate the snapshot and apply the catalog change
and decision in one transaction. Invalid requests return `400`, denied roles
`403`, missing/out-of-scope request `404`, state/uniqueness conflicts `409`,
missing repository `501`.

`POST /api/sales` accepts `branch_id` and a non-empty `items` array with
`product_id`, `product_variant_id`, and positive `quantity`; server catalog
prices are snapshotted and client prices are never authoritative. Requires
`Idempotency-Key`. The atomic repository must save the sale, decrement only
available stock, append ledger rows, and persist the request/result together.
Returns `201` after commit, `400` invalid input or insufficient stock, `403`
denied, `404` scoped resource missing, `409` key conflict, `501` without the
operations adapter.

`GET /api/sales` accepts `sale_reference`, `date_from`, `date_to`,
`responsible_user_id`, `branch_id`, `status` (`Completed` or `Cancelled`),
`sort_order` (`asc`/`desc`), `page`, and `per_page`. `GET /api/sales/{sale_id}`
returns a snapshot-based detail. Requires `sales:view` for Owners/Managers or
`sales:view_authorized` for Employees. The pure history rules scope rows before
filtering and pagination; invalid filters return `400`, foreign IDs are
concealed as `404`, and missing repository returns `501`.

Employees submit cancellation requests using
`POST /api/sales/{sale_id}/cancellation-requests` with `{"reason":"..."}`.
Owners and same-branch Managers decide using
`POST /api/sale-cancellation-requests/{request_id}/approve` or `/reject`; reject
requires `{"reason":"..."}`. Only completed sales can be requested. An
approval restores sold quantities less cumulative returns, and the repository
must atomically update request/sale status, ledger, balances, and idempotency
record. Repeated pending requests and invalid transitions return `409`;
authorization is `403`; missing repository is `501`.
`GET /api/sale-cancellation-requests` lists the caller-visible queue and accepts
optional `status` (`Pending`, `Approved`, or `Rejected`); Employees see only
their branch requests while Owners/Managers receive only scopes they may
review. Invalid filters return `400` and missing persistence returns `501`.

`POST /api/returns` accepts `reason`, `items` (variant ID and quantity), and
exactly one of `original_sale_id` or `branch_id` for an unlinked return. Linked
returns must match completed sales and cumulative quantities cannot exceed sold
quantity. Unlinked returns start pending and require
`POST /api/returns/{return_id}/verify` by an Owner or in-scope Manager before
inspection. Inspect with `POST /api/returns/{return_id}/inspections` and
`{"items":[{"return_item_id":1,"quantity":1,"condition":"RESALABLE"}]}`;
allowed conditions are `RESALABLE`, `DAMAGED`, and `UNDER_INSPECTION`. Only
resalable quantities become available stock; damaged and under-inspection
quantities remain unavailable. Submissions and inspections require
`Idempotency-Key`; the repository must persist return/inspection, movements,
and idempotency state atomically. Invalid/over-return quantities return `400`,
denied scope `403`, missing resource `404`, duplicates/state conflicts `409`,
and missing persistence `501`.
`GET /api/returns` accepts optional `status` (`Pending`, `Verified`,
`Partially Inspected`, `Processed`, or `Rejected`) and returns only records
visible to the current role and branch. Missing persistence returns `501`.

The OperationsRepository contract is an integration port only; it does not
contain ORM models or migrations. It requires atomic write methods for each
domain event, business-scoped uniqueness/idempotency, locked stock rechecks,
ordered balance locks, and rollback on any failure. Production availability
depends on the Database team's approved schema and a persistent shared adapter.

Operational successful collection reads use `{"data":[...]}`; single reads and
successful writes use `{"data":{...}}`, with writes also returning `message`.
Write data is the saved resource snapshot, never a request echo. The repository
must return JSON-safe snapshots without ORM objects, password hashes, session
tokens, CSRF values, or database exception text. Sales list rows use the fields
returned by the history rules (`sale_id`, `sale_reference`, `sale_date`,
`branch_id`, `branch_name`, `responsible_user_id`, `responsible_user_name`,
`status`, `total_amount`); detail adds the historical product/variant lines and
price snapshots. Product overview returns `product_id`, `name`, `product_code`,
`category_id`, `category_name`, `selling_price`, nullable `cost_price`,
`total_available_qty`, `variants`, `branch_stock`, and `recent_movements`.
Product request records include `request_id`, `change_type`, `reason`, the
immutable `payload` snapshot, `status`, and available review comments/reasons.
Cancellation and return records include their resource ID, source sale/branch,
status, reason, requested lines, and timestamps; inspection results include
classified returned quantities. Inventory adjustment responses additionally
set `X-Expected-Available-Quantity` to the validated post-adjustment available
balance.
