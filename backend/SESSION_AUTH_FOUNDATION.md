# BE-10 Part 1: Session Authentication Foundation

This document describes the reusable authentication foundation in
`session_auth.py` and the route orchestration in `auth_workflows.py` and
`routes/auth.py`. The default `create_app()` installs no authentication,
session, or rate-limit adapters; without them, the routes retain their
explicit `501` behavior. No MySQL persistence adapter is implemented here.

## Reused security and authorization rules

- `security.py` already provides Werkzeug password hashing and verification.
  `auth_workflows.py` reuses `verify_password` and `hash_password`; it adds no
  second password mechanism.
- `permissions.py` already defines `AuthenticatedPrincipal`, the role matrix,
  and fail-closed trust checks. The future database user loader must build a
  fresh principal from stored role, business, branch, and permission data.
- `isolation_rules.py` and `onboarding_rules.py` continue to consume that
  trusted principal and server-loaded resource scopes. Session validation does
  not change those policies.
- Without explicitly installed adapters, the existing validation and `501`
  authentication responses are unchanged. Configured workflows use the
  response shapes listed in `API_CONTRACT.md`.

## Session behavior

- A session token is generated with `secrets.token_urlsafe(32)`. The browser
  receives the opaque token; the server store is keyed by its SHA-256 digest.
- A session record contains only `user_id`, `access_version_at_login`,
  `expires_at`, and a digest of a separate CSRF token. It contains no password,
  role, permissions, business ID, branch ID, or raw token.
- The default lifetime is fixed at 12 hours, with a seven-day maximum. Sessions
  do not slide or renew automatically. An expired session is deleted during
  validation.
- Each validation loads the user again through an injected trusted callback.
  Missing, disabled, deleted, malformed, or version-mismatched users invalidate
  and delete the session. The returned principal therefore reflects current
  server-side role and branch assignments, not cookie or request values.
- `auth_workflows.py` defines the `AuthRepository` boundary for case-insensitive
  email lookup and atomic Owner/business registration. It creates no model or
  persistence adapter. A production repository must persist the business and
  branchless Owner in one transaction, enforce email uniqueness under
  concurrency, and return trusted principal and business-scoped branch-count
  data for login.
- Login orchestration uses the repository's stored hash and approved
  `users.access_version`, the existing `SessionManager`, and
  `onboarding_rules.py`. A new session copies that value into
  `access_version_at_login`, matching the approved
  `auth_sessions.access_version_at_login` contract. Successful login sets the
  existing session/CSRF cookies and reports when an Owner must create the
  first branch. Registration never creates a session or branch.
- Logout is connected when a session manager is installed. It requires the
  existing session and CSRF checks, revokes the server-side record, and clears
  both cookies. A missing manager preserves the existing `501` response.
- The route requires the existing `LoginRateLimiter` as well as account and
  session adapters. The limiter fails closed if its shared store errors. In
  production, adapter installers require an explicit `is_persistent = True`
  declaration; no in-memory production fallback is provided.
- There is no production in-memory fallback. `SessionStore` is an interface;
  the only in-memory implementation in this change is local to pytest.

## Cookies and CSRF

`set_session_cookies` sets a host-only `branchwise_session` cookie with
`HttpOnly`, `SameSite=Lax`, path `/api`, and the fixed lifetime. It sets a
separate `branchwise_csrf` cookie with `SameSite=Lax` and the same scope and
lifetime; this token is readable by browser code and cannot authenticate by
itself. Both cookies set `Secure` when called with `production=True`. No
`Domain` attribute is added.

BE-51 adds app configuration validation: production requires
`APP_ENV="production"`, `SESSION_COOKIE_SECURE=True`, and `DEBUG=False`.
The login route passes the validated secure-cookie setting to the cookie
helper. Production must serve cookies only over HTTPS. The deployment's TLS
terminator must enforce HTTPS; HSTS is left to that terminator because this app
does not yet know the trusted proxy/TLS setup.

`require_authenticated_session` resolves identity only from the session cookie.
It checks that unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`, and other
non-safe methods) send the same CSRF token in the readable CSRF cookie and the
`X-CSRF-Token` header, and that the token matches the hash in that session's
server-side record. `GET`, `HEAD`, and `OPTIONS` do not require CSRF. The
decorator exposes the trusted context as `g.authenticated_session`.

The decorator returns `401` when no valid session manager or session exists,
and `403` for a failed CSRF check. It protects the logout route when a session
manager is installed; business routes are not yet decorated.

There is no application CORS configuration. Browser clients must use the same
origin when cookie sessions are integrated unless a reviewed exact-origin
allowlist is added. Credentialed wildcard CORS is not permitted. CSRF remains
the session-bound mechanism above; BE-51 does not introduce another one.

## Decisions required for database integration

1. Approve the persistent `SessionStore` backend and its operational properties
   (atomic create/replace/delete, expiry cleanup, backup/retention, and access
   control). The in-memory session record also stores a CSRF-token digest;
   the reference `auth_sessions` definition does not include that field, so
   its persistent storage mapping still needs approval. This change creates no
   database tables and chooses no storage product.
2. Keep the approved version mapping: read `users.access_version` into
   `AuthAccount.access_version` and store its login-time value as
   `SessionRecord.access_version_at_login`, mapped to
   `auth_sessions.access_version_at_login`. Increment `users.access_version`
   in the same transaction as role, branch, password, or status changes, as
   the database contract requires. A mismatch lazily invalidates and deletes
   the session. This is an adapter mapping only; it does not require a schema
   change.
3. Implement the user loader so it fetches the account on each validation,
   checks active/deleted state and the current `access_version` against the
   session's saved version, then constructs the
   `AuthenticatedPrincipal` from current trusted database values. Never accept
   user IDs, roles, business IDs, branch assignments, or permissions from a
   request as the loader's authority.
4. Implement the production `AuthRepository`: canonical email lookup, atomic
   Owner/business creation without a branch, duplicate-email enforcement,
   stored password hash and access version, trusted principal, and business
   branch count. The route workflow is prepared but no adapter is configured.
5. Install production repository, `SessionStore`/user loader, and atomic shared
   rate-limit store/key only after those adapters are approved. The route
   already verifies credentials, rejects inactive/deleted accounts, issues
   sessions, sets cookies, enforces the limiter, and reports first-branch
   onboarding when all adapters exist.
6. Logout orchestration is connected as a CSRF-protected state-changing
   request: it revokes the presented server-side token and clears both cookies
   when session persistence is installed.
7. Integrate role, branch, password, and status changes with the approved
   `access_version` increment and audit transaction. Fresh user loading applies
   current permissions on the next request; the version increment also forces
   old sessions to reauthenticate, as specified by the database contract.
8. Configure production deployment so TLS is terminated safely and the cookie
   helper receives `production=True`; choose the deployment's approved CSRF
   token delivery to same-origin browser code before activating state-changing
   routes.

Until production adapters are configured, the default login/register/logout
routes retain explicit `501` behavior. No session-based access to business API
routes is active.
