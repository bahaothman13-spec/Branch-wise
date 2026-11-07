"""Server-side session authentication foundations for BranchWise.

This module deliberately provides no production session-store implementation
and does not authenticate credentials. The caller must supply a trusted user
record after credential verification, a persistent ``SessionStore``, and a
server-side user loader. No browser cookie or stored session contains roles,
permissions, or business/branch authorization data.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Protocol

from flask import current_app, g, request

from permissions import AuthenticatedPrincipal, is_trusted_principal
from request_validation import is_positive_integer
from response_helpers import error_response


DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_TOKEN_BYTES = 32
SESSION_COOKIE_NAME = "branchwise_session"
CSRF_COOKIE_NAME = "branchwise_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_AUTH_EXTENSION = "branchwise.session_manager"
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class TrustedSessionUser:
    """Current account identity loaded from trusted server-side data.

    A future credential flow may pass this value to ``create_session`` only
    after verifying the password. A future ``user_loader`` must construct it
    from the database, including a fresh principal built from current role,
    business, and branch assignments.
    """

    user_id: int
    access_version: int
    is_active: bool
    is_deleted: bool
    principal: AuthenticatedPrincipal


@dataclass(frozen=True)
class SessionRecord:
    """Minimal server-side session data; never serialize this into a cookie."""

    user_id: int
    access_version_at_login: int
    expires_at: datetime
    csrf_token_digest: str = field(repr=False)


class SessionStore(Protocol):
    """Minimal interface for an approved persistent server-side session store.

    A production implementation must declare ``is_persistent = True`` so the
    installer can reject test-local or in-memory stores in production.
    """

    def get(self, token_digest: str) -> SessionRecord | None:
        """Return the record for a token digest, or ``None`` if absent."""

    def put(self, token_digest: str, record: SessionRecord) -> None:
        """Create or replace a record under its token digest."""

    def delete(self, token_digest: str) -> None:
        """Delete a record, making that session unusable immediately."""


@dataclass(frozen=True)
class IssuedSession:
    """One-time values returned when a trusted account session is created."""

    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime
    max_age_seconds: int


@dataclass(frozen=True)
class AuthenticatedSession:
    """Trusted request context resolved from a valid server-side session."""

    user_id: int
    principal: AuthenticatedPrincipal
    expires_at: datetime
    _session_token_digest: str = field(repr=False)
    _csrf_token_digest: str = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Session timestamps must be timezone-aware.")
    if value.utcoffset() is None:
        raise ValueError("Session timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _is_valid_token(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_PATTERN.fullmatch(token) is not None


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _valid_trusted_user(user: object) -> bool:
    return (
        type(user) is TrustedSessionUser
        and is_positive_integer(user.user_id)
        and type(user.access_version) is int
        and user.access_version >= 0
        and type(user.is_active) is bool
        and type(user.is_deleted) is bool
        and is_trusted_principal(user.principal)
    )


class SessionManager:
    """Create, validate, and revoke sessions using injected trusted services."""

    def __init__(
        self,
        store: SessionStore,
        user_loader: Callable[[int], TrustedSessionUser | None],
        *,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not all(callable(getattr(store, method, None)) for method in ("get", "put", "delete")):
            raise TypeError("store must implement get, put, and delete.")
        if not callable(user_loader):
            raise TypeError("user_loader must be callable.")
        if (
            type(ttl_seconds) is not int
            or ttl_seconds <= 0
            or ttl_seconds > MAX_SESSION_TTL_SECONDS
        ):
            raise ValueError("ttl_seconds must be between 1 second and 7 days.")
        if not callable(clock):
            raise TypeError("clock must be callable.")

        self._store = store
        self._user_loader = user_loader
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def create_session(self, user: TrustedSessionUser) -> IssuedSession:
        """Create a session for a verified, active account supplied by caller.

        The caller is responsible for verifying credentials before this call.
        Only a SHA-256 token digest and minimal invalidation metadata are saved.
        """
        if not _valid_trusted_user(user) or not user.is_active or user.is_deleted:
            raise ValueError("An active trusted user is required.")

        now = _as_utc(self._clock())
        expires_at = now + timedelta(seconds=self._ttl_seconds)
        for _ in range(5):
            session_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
            csrf_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
            if not _is_valid_token(session_token) or not _is_valid_token(csrf_token):
                continue

            digest = _token_digest(session_token)
            if self._store.get(digest) is not None:
                continue

            self._store.put(
                digest,
                SessionRecord(
                    user_id=user.user_id,
                    access_version_at_login=user.access_version,
                    expires_at=expires_at,
                    csrf_token_digest=_token_digest(csrf_token),
                ),
            )
            return IssuedSession(
                session_token=session_token,
                csrf_token=csrf_token,
                expires_at=expires_at,
                max_age_seconds=self._ttl_seconds,
            )

        raise RuntimeError("Could not allocate a unique session token.")

    def resolve_session(self, session_token: str | None) -> AuthenticatedSession | None:
        """Resolve fresh trusted user context, failing closed on invalid state."""
        if not _is_valid_token(session_token):
            return None

        digest = _token_digest(session_token)
        record = self._store.get(digest)
        if not self._is_valid_record(record):
            if record is not None:
                self._store.delete(digest)
            return None

        now = _as_utc(self._clock())
        expires_at = _as_utc(record.expires_at)
        if now >= expires_at:
            self._store.delete(digest)
            return None

        # This callback must query authoritative account data on every request.
        user = self._user_loader(record.user_id)
        if (
            not _valid_trusted_user(user)
            or not user.is_active
            or user.is_deleted
            or user.user_id != record.user_id
            or user.access_version != record.access_version_at_login
        ):
            self._store.delete(digest)
            return None

        return AuthenticatedSession(
            user_id=user.user_id,
            principal=user.principal,
            expires_at=expires_at,
            _session_token_digest=digest,
            _csrf_token_digest=record.csrf_token_digest,
        )

    def revoke_session(self, session_token: str | None) -> bool:
        """Delete a validly-shaped token's record; repeated logout is safe."""
        if not _is_valid_token(session_token):
            return False
        self._store.delete(_token_digest(session_token))
        return True

    @staticmethod
    def csrf_token_is_valid(
        context: AuthenticatedSession,
        csrf_cookie: str | None,
        csrf_header: str | None,
    ) -> bool:
        """Require a session-bound double-submit token for unsafe methods."""
        if (
            type(context) is not AuthenticatedSession
            or not _is_valid_token(csrf_cookie)
            or not _is_valid_token(csrf_header)
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            return False
        return hmac.compare_digest(
            _token_digest(csrf_cookie), context._csrf_token_digest
        )

    @staticmethod
    def _is_valid_record(record: object) -> bool:
        return (
            type(record) is SessionRecord
            and is_positive_integer(record.user_id)
            and type(record.access_version_at_login) is int
            and record.access_version_at_login >= 0
            and isinstance(record.expires_at, datetime)
            and record.expires_at.tzinfo is not None
            and record.expires_at.utcoffset() is not None
            and isinstance(record.csrf_token_digest, str)
            and _DIGEST_PATTERN.fullmatch(record.csrf_token_digest) is not None
        )


def install_session_auth(
    app,
    store: SessionStore,
    user_loader: Callable[[int], TrustedSessionUser | None],
    *,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    clock: Callable[[], datetime] = _utc_now,
) -> SessionManager:
    """Explicitly install an injected session manager into a Flask app.

    ``create_app`` intentionally does not call this until a persistent store
    and authoritative database user loader have been approved and integrated.
    """
    if (
        app.config.get("APP_ENV") == "production"
        and getattr(store, "is_persistent", False) is not True
    ):
        raise ValueError("Production sessions require persistent storage.")

    manager = SessionManager(
        store,
        user_loader,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )
    app.extensions[SESSION_AUTH_EXTENSION] = manager
    return manager


def require_authenticated_session(view):
    """Protect a future Flask route with a session cookie and CSRF checks.

    The resolved trusted context is available as ``g.authenticated_session``.
    State-changing methods require the matching readable CSRF cookie and
    ``X-CSRF-Token`` header. Routes remain unprotected until explicitly wrapped
    and a session manager is installed with trusted services.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        manager = current_app.extensions.get(SESSION_AUTH_EXTENSION)
        if manager is None:
            return error_response("Authentication is required.", status_code=401)

        context = manager.resolve_session(request.cookies.get(SESSION_COOKIE_NAME))
        if context is None:
            return error_response("Authentication is required.", status_code=401)

        if request.method.upper() not in _CSRF_SAFE_METHODS and not manager.csrf_token_is_valid(
            context,
            request.cookies.get(CSRF_COOKIE_NAME),
            request.headers.get(CSRF_HEADER_NAME),
        ):
            return error_response("CSRF validation failed.", status_code=403)

        g.authenticated_session = context
        return view(*args, **kwargs)

    return wrapped


def set_session_cookies(response, issued: IssuedSession, *, production: bool):
    """Set host-only session and readable CSRF cookies with safe attributes."""
    if type(production) is not bool:
        raise TypeError("production must be a Boolean value.")
    if type(issued) is not IssuedSession:
        raise TypeError("issued must be an IssuedSession.")

    shared = {
        "secure": production,
        "samesite": "Lax",
        "path": "/api",
        "max_age": issued.max_age_seconds,
        "expires": issued.expires_at,
    }
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.session_token,
        httponly=True,
        **shared,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        issued.csrf_token,
        httponly=False,
        **shared,
    )
    return response


def clear_session_cookies(response, *, production: bool):
    """Expire both cookies after logout, using the same scope and attributes."""
    if type(production) is not bool:
        raise TypeError("production must be a Boolean value.")
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=production,
        httponly=True,
        samesite="Lax",
        path="/api",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        secure=production,
        httponly=False,
        samesite="Lax",
        path="/api",
    )
    return response


__all__ = [
    "AuthenticatedSession",
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "DEFAULT_SESSION_TTL_SECONDS",
    "IssuedSession",
    "MAX_SESSION_TTL_SECONDS",
    "SESSION_AUTH_EXTENSION",
    "SESSION_COOKIE_NAME",
    "SESSION_TOKEN_BYTES",
    "SessionManager",
    "SessionRecord",
    "SessionStore",
    "TrustedSessionUser",
    "clear_session_cookies",
    "install_session_auth",
    "require_authenticated_session",
    "set_session_cookies",
]
