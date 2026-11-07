# BranchWise API Contract

This document describes the Flask API that currently exists in `backend/routes`
and conventions for extending it. It is based on those routes and
`backend/ROLE_PERMISSIONS.md`; no separate approved user-story document is
present in this repository. It does not imply that the unimplemented business
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

For routes that require a JSON body, a missing or non-JSON content type returns
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
`APP_ENV="production"`, `SESSION_COOKIE_SECURE=True`, and `DEBUG=False`.
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
rate-limit store is configured. Session-backed authentication is not attached
to business routes; their route-level RBAC and tenant isolation remain pending.
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
| `200` | Request completed successfully; used by `/api/health`. |
| `201` | A resource was created successfully. Owner registration returns this only when its repository completes the atomic write. |
| `400` | Malformed request, invalid JSON shape, or current field/path validation failure. Existing validation endpoints use this status for compatibility. |
| `401` | Authentication is missing or invalid. Login returns this for invalid credentials; logout returns it for a missing or invalid session. Other business routes are not yet protected. |
| `403` | An authenticated user lacks permission, or a session-backed state change fails CSRF validation. Logout uses the latter; business-route RBAC is not yet active. |
| `404` | The route or resource was not found. Unknown routes return a generic JSON error. When resource scoping is implemented, use `404` for inaccessible cross-business resources if revealing their existence would leak information. |
| `409` | A request conflicts with existing state, including duplicate email during owner registration. |
| `429` | Login attempts exceeded an installed account or client rate limit. |
| `413` | Request body exceeds the configured maximum size; returns a generic JSON error. |
| `422` | A syntactically valid request fails semantic validation. Existing validation uses `400`; retain that behavior unless a contract change is approved. |
| `500` | Unexpected server failure. The response is generic and must not include an exception, stack trace, SQL, credentials, or password. |
| `501` | The endpoint is recognized but its operation is not implemented. Several current endpoints use this while authentication or database support is pending. |
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

No current list route returns a paginated dataset. When pagination is added,
use query parameters `page` (1-based, default `1`) and `per_page` (default `20`,
maximum `100`). Return the requested values and totals alongside `data`, for
example:

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

List filters should use documented, resource-specific `snake_case` query
parameters, and only explicitly supported filters should affect results.
Reject invalid page numbers, page sizes, and filter values using the validation
error convention. Pagination and filtering described here are conventions for
future list behavior, not features already implemented.

## Current routes

| Method and path | Current behavior |
| --- | --- |
| `GET /api/health` | Returns `200` with `{"status":"ok"}`. |
| `POST /api/auth/register` | Accepts `owner_name`, `business_name`, `email`, and `password`; validates input (`400`); when an `AuthRepository` is installed, atomically creates the Owner and business without a branch (`201`), or returns `409` for a duplicate email. Repository failures return a generic `500`. Without a repository it retains `501`. |
| `POST /api/auth/login` | Accepts `email` and `password`; validates input (`400`); requires installed account, session, and rate-limit adapters. Invalid or inactive credentials return `401`, rate limiting returns `429`, adapter failures return generic `500`/`503`, and success sets session and CSRF cookies and reports whether first-branch onboarding is required. Missing adapters retain `501`. |
| `POST /api/auth/logout` | Requires an installed session manager, valid session, and matching CSRF cookie/header; revokes the session and clears both cookies. Persistence failures return a generic `500`. Missing session management retains `501`; unauthenticated requests return `401`, and failed CSRF validation returns `403`. |
| `GET /api/branches` | Returns `501` pending authentication and database integration. |
| `POST /api/branches` | Accepts `name`, `address`, `phone`, and `notes`; validates input (`400` on invalid input); otherwise returns `501`. |
| `GET /api/branches/{branch_id}` | Validates a positive integer ID (`400` if invalid); otherwise returns `501`. |
| `PATCH /api/branches/{branch_id}` | Accepts `name`, `address`, `phone`, and `notes`; validates the ID and editable fields (`400` if invalid); otherwise returns `501`. |
| `PATCH /api/branches/{branch_id}/deactivate` | Validates a positive integer ID (`400` if invalid); otherwise returns `501`. |
| `POST /api/users` | Accepts `name`, `email`, `role`, `initial_password`, and `branch_id`; validates input (`400` on invalid input); otherwise returns `501`. |
| `PATCH /api/users/{user_id}/status` | Accepts `is_active`; validates the ID and status value (`400` if invalid); otherwise returns `501`. |
| `PATCH /api/users/me/password` | Accepts `current_password`, `new_password`, and `confirm_password`; validates input (`400` on invalid input); otherwise returns `501`. |
