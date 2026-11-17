# BE-51 Part 1: API Security Foundations

This note records protections prepared by BE-51 Part 1 and distinguishes them
from controls that still require real authentication and infrastructure.

## Implemented

- Flask caps request bodies at 1 MiB by default. `MAX_CONTENT_LENGTH` can be
  configured up to 16 MiB; oversized requests receive a generic JSON `413`.
- All responses receive conservative browser headers: content-type sniffing is
  disabled, framing is denied, referrers are suppressed, browser capabilities
  are restricted, the API content policy disables active content, and API
  responses are marked `Cache-Control: no-store`.
- HTTP and unexpected application errors use generic JSON. Unexpected request
  exceptions are not written to application logs with exception text or
  traceback. Existing database connection logging also avoids exception text
  and credential values.
- Current JSON endpoints validate fields against explicit allowlists. Unknown
  input keys produce a generic `errors.body` validation error; key names and
  values are not echoed.
- `rate_limiting.py` defines configurable per-identifier and per-client login
  limits, an HMAC-derived opaque bucket key, and an explicit atomic store
  protocol. The default rule is 5 attempts per identifier and 30 per client
  address per 15 minutes. There is no production in-memory fallback.
- Existing password hashing, principal/RBAC and business isolation rules,
  session management, cookie helpers, and session-bound CSRF validation are
  reused by the prepared authentication workflows. Flask test-client tests
  exercise these workflows with test-local repositories and stores.
- The API does not enable CORS. There is no wildcard origin and no credentialed
  cross-origin access.
- Passwords are limited to 1,024 characters before hashing or authentication,
  so a request cannot force unbounded password-hash work.

## Configuration and deployment

The app factory accepts Flask configuration and validates these settings:

| Setting | Default | Production requirement |
| --- | --- | --- |
| `MAX_CONTENT_LENGTH` | 1 MiB | Positive integer, no greater than 16 MiB |
| `APP_ENV` | `development` | `production` |
| `SESSION_COOKIE_SECURE` | `False` | `True` |
| `FLASK_DEBUG` (environment) / `DEBUG` (app config) | `False` | `False` |
| `SECRET_KEY` | unset | Random value of at least 32 bytes |
| `LOGIN_RATE_LIMIT_IDENTIFIER_LIMIT` | `5` | Configure to the approved policy |
| `LOGIN_RATE_LIMIT_CLIENT_LIMIT` | `30` | Configure to the approved policy |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` | Configure to the approved policy |

`APP_ENV`, `SESSION_COOKIE_SECURE`, `FLASK_DEBUG`, and `SECRET_KEY` are read
from the process environment or backend `.env`; explicit app-factory config
overrides them. `FLASK_DEBUG` maps to the Flask `DEBUG` config value. Invalid
Boolean values fail app startup. Production startup
fails when secure cookies, disabled debug mode, or a sufficiently long signing
secret is missing, or when SQLAlchemy would use the in-memory SQLite fallback.
Do not start Flask CLI with `--debug` in production. Production must point at a
durable account database configured for the deployment.

The session cookie helper already sets the authentication cookie `HttpOnly`,
keeps cookies host-only and scoped to `/api`, and uses `SameSite=Lax`; the CSRF
cookie is readable by same-origin browser code. Both use `Secure` when the
helper receives `production=True`. Production deployment must terminate TLS
safely, set the secure cookie option, and only then integrate cookie issuance.
HSTS must be configured at the TLS-terminating ingress once HTTPS is assured.

## Awaiting integration

- `/api/auth/login` invokes the prepared limiter and authentication workflow
  only when the account repository, session manager, and limiter are explicitly
  installed. The default app installs none, so it retains `501`. Production
  requires a reliable atomic shared store for every worker and a secret of at
  least 32 bytes. Store failure returns `503` before credential lookup; there
  is no local-memory fallback.
- `auth_workflows.py` defines the repository contract and uses the existing
  password helpers, `SessionManager`, and onboarding policy. No real account,
  session, or rate-limit persistence adapter is configured. Production session
  stores must declare persistence and sharing; rate-limit stores must declare
  persistent, shared, atomic counters. Missing or unhealthy dependencies fail
  closed before authentication.
- Logout uses the existing session and CSRF checks to revoke the session and
  clear cookies when a session manager is installed; without it, the route
  retains `501`.
- Current branch and user business routes are not decorated with authentication
  or authorization and continue returning `501` for valid requests. RBAC and
  business/branch isolation remain reusable policy code. Test-only Flask routes
  verify that policy is applied to the principal loaded from a session and to
  resource scopes returned by an injected fake repository; they do not activate
  or persist business routes.
- No MySQL connection, schema, or database-backed rate/session store was added.
- CSRF checks from `session_auth.py` protect logout when sessions are installed.
  Other business routes do not have authenticated sessions.
- CORS remains disabled. If deployment later needs cross-origin browser access,
  define and validate exact trusted origins and never use wildcard CORS with
  credentials.

Therefore, this work does not claim that all API endpoints are secured.
