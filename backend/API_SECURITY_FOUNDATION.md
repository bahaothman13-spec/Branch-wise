# BE-51 Part 1: API Security Foundations

This note records protections prepared by BE-51 Part 1 and distinguishes them
from controls that still require real authentication and infrastructure.

## Implemented

- Flask caps request bodies at 1 MiB by default. `MAX_CONTENT_LENGTH` can be
  configured up to 16 MiB; oversized requests receive a generic JSON `413`.
- All responses receive conservative browser headers: content-type sniffing is
  disabled, framing is denied, referrers are suppressed, browser capabilities
  are restricted, and the API content policy disables active content.
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
  session foundation, cookie helpers, and session-bound CSRF validation are
  reused. BE-51 does not implement a second password or CSRF mechanism.
- The API does not enable CORS. There is no wildcard origin and no credentialed
  cross-origin access.

## Configuration and deployment

The app factory accepts Flask configuration and validates these settings:

| Setting | Default | Production requirement |
| --- | --- | --- |
| `MAX_CONTENT_LENGTH` | 1 MiB | Positive integer, no greater than 16 MiB |
| `APP_ENV` | `development` | `production` |
| `SESSION_COOKIE_SECURE` | `False` | `True` |
| `DEBUG` | Flask default (`False`) | `False` |
| `LOGIN_RATE_LIMIT_IDENTIFIER_LIMIT` | `5` | Configure to the approved policy |
| `LOGIN_RATE_LIMIT_CLIENT_LIMIT` | `30` | Configure to the approved policy |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `900` | Configure to the approved policy |

The session cookie helper already sets the authentication cookie `HttpOnly`,
keeps cookies host-only and scoped to `/api`, and uses `SameSite=Lax`; the CSRF
cookie is readable by same-origin browser code. Both use `Secure` when the
helper receives `production=True`. Production deployment must terminate TLS
safely, set the secure cookie option, and only then integrate cookie issuance.
HSTS must be configured at the TLS-terminating ingress once HTTPS is assured.

## Awaiting integration

- `/api/auth/login` remains a placeholder and does not invoke the prepared
  limiter. Before enabling it, provide a reliable atomic shared store suitable
  for every production worker and a secret of at least 32 bytes for HMAC bucket
  keys. Store failure must fail closed; it must not fall back to local memory.
- There is no real credential lookup, password verification on the route,
  session issuance, logout revocation, persistent session store, or trusted
  database user loader.
- Current business routes are not decorated with authentication or
  authorization. The existing RBAC and isolation rules remain reusable policy
  code, not active route protection.
- No MySQL connection, schema, or database-backed rate/session store was added.
- CSRF checks are provided by `session_auth.py` for future decorated session
  routes only; current routes do not have authenticated sessions.
- CORS remains disabled. If deployment later needs cross-origin browser access,
  define and validate exact trusted origins and never use wildcard CORS with
  credentials.

Therefore, this work does not claim that all API endpoints are secured.
