# BE-10 Part 1: Session Authentication Foundation

This document describes the reusable authentication foundation in
`session_auth.py`. It does not claim that login, logout, or protected API
authentication is active. The existing `/api/auth/login` and
`/api/auth/logout` endpoints remain pending implementation, and
`create_app()` does not install a session manager.

## Reused security and authorization rules

- `security.py` already provides Werkzeug password hashing and verification.
  A later login flow should use `verify_password`; this foundation does not
  inspect credentials or add another password mechanism.
- `permissions.py` already defines `AuthenticatedPrincipal`, the role matrix,
  and fail-closed trust checks. The future database user loader must build a
  fresh principal from stored role, business, branch, and permission data.
- `isolation_rules.py` and `onboarding_rules.py` continue to consume that
  trusted principal and server-loaded resource scopes. Session validation does
  not change those policies.
- Existing API response shapes and the current `400`/`501` authentication
  endpoint behavior are unchanged.

## Session behavior

- A session token is generated with `secrets.token_urlsafe(32)`. The browser
  receives the opaque token; the server store is keyed by its SHA-256 digest.
- A session record contains only `user_id`, `auth_version`, `expires_at`, and a
  digest of a separate CSRF token. It contains no password, role, permissions,
  business ID, branch ID, or raw token.
- The default lifetime is fixed at 12 hours, with a seven-day maximum. Sessions
  do not slide or renew automatically. An expired session is deleted during
  validation.
- Each validation loads the user again through an injected trusted callback.
  Missing, disabled, deleted, malformed, or version-mismatched users invalidate
  and delete the session. The returned principal therefore reflects current
  server-side role and branch assignments, not cookie or request values.
- Logout support is provided at the service level by deleting the server-side
  record. Cookie clearing uses the same path and security attributes. The API
  logout route is not connected yet.
- There is no production in-memory fallback. `SessionStore` is an interface;
  the only in-memory implementation in this change is local to pytest.

## Cookies and CSRF

`set_session_cookies` sets a host-only `branchwise_session` cookie with
`HttpOnly`, `SameSite=Lax`, path `/api`, and the fixed lifetime. It sets a
separate `branchwise_csrf` cookie with `SameSite=Lax` and the same scope and
lifetime; this token is readable by browser code and cannot authenticate by
itself. Both cookies set `Secure` when called with `production=True`. No
`Domain` attribute is added.

`require_authenticated_session` resolves identity only from the session cookie.
It checks that unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`, and other
non-safe methods) send the same CSRF token in the readable CSRF cookie and the
`X-CSRF-Token` header, and that the token matches the hash in that session's
server-side record. `GET`, `HEAD`, and `OPTIONS` do not require CSRF. The
decorator exposes the trusted context as `g.authenticated_session`.

The decorator returns `401` when no valid session manager or session exists,
and `403` for a failed CSRF check. It is not attached to any current route.

## Decisions required for database integration

1. Approve the persistent `SessionStore` backend and its operational properties
   (atomic create/replace/delete, expiry cleanup, backup/retention, and access
   control). This change creates no database tables and chooses no storage
   product.
2. Add a monotonic per-user `auth_version` (or equivalent credential version)
   to the approved user schema. Increment it in the same transaction as
   password changes/resets and account disable/delete changes. Incrementing it
   after reactivation prevents an old pre-disable session from becoming valid
   again. A version mismatch lazily invalidates every session for that user.
3. Implement the user loader so it fetches the account on each validation,
   checks active/deleted state and `auth_version`, then constructs the
   `AuthenticatedPrincipal` from current trusted database values. Never accept
   user IDs, roles, business IDs, branch assignments, or permissions from a
   request as the loader's authority.
4. Implement login only after approved account lookup is available: validate
   input, verify the stored password with `security.verify_password`, reject
   inactive/deleted accounts, then call `SessionManager.create_session` and
   set cookies. Do not issue sessions for placeholder credentials.
5. Integrate logout as a CSRF-protected state-changing request: revoke the
   presented server-side token and clear both cookies. Preserve the approved
   response contract when the endpoint is implemented.
6. Integrate password change and user status changes with `auth_version`
   increments, and explicitly decide whether role or branch reassignment should
   also force reauthentication. Fresh user loading already applies current
   permissions on the next request.
7. Configure production deployment so TLS is terminated safely and the cookie
   helper receives `production=True`; choose the deployment's approved CSRF
   token delivery to same-origin browser code before activating state-changing
   routes.

Until those pieces are integrated, the current login/logout routes and other
API routes retain their existing behavior and no session-based API access is
active.
