"""Reusable login rate-limit rules with an explicitly injected store.

This module intentionally has no in-memory or production store and is not
connected to the placeholder login route. Store implementations must provide
an atomic, shared consume operation before these rules can protect production
authentication.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol


LOGIN_RATE_LIMIT_EXTENSION = "branchwise.rate_limiter"


@dataclass(frozen=True)
class LoginRateLimitConfig:
    """Attempt limits applied per normalized account and per client address."""

    identifier_limit: int = 5
    client_limit: int = 30
    window_seconds: int = 15 * 60

    def __post_init__(self):
        for name in ("identifier_limit", "client_limit", "window_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        if self.identifier_limit > 1000 or self.client_limit > 10000:
            raise ValueError("Login attempt limits are too large.")
        if self.window_seconds > 24 * 60 * 60:
            raise ValueError("window_seconds cannot exceed one day.")


@dataclass(frozen=True)
class RateLimitDecision:
    """Store decision after atomically counting one attempt in a bucket."""

    allowed: bool
    remaining: int
    retry_after_seconds: int = 0

    def __post_init__(self):
        if type(self.allowed) is not bool:
            raise ValueError("allowed must be a Boolean value.")
        if type(self.remaining) is not int or self.remaining < 0:
            raise ValueError("remaining must be a non-negative integer.")
        if type(self.retry_after_seconds) is not int or self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be a non-negative integer.")


class RateLimitStore(Protocol):
    """Explicit shared-store interface; consume must be atomic per key.

    The store increments a bucket within a fixed window and returns whether
    the attempt is allowed. A production implementation must share counters
    across all API workers, expire old buckets reliably, and declare
    ``is_persistent = True`` for installation in production.
    """

    def consume(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        """Count one attempt and return its decision and retry metadata."""


class LoginRateLimiter:
    """Apply account and client limits without retaining raw identifiers."""

    def __init__(
        self,
        store: RateLimitStore,
        key_secret: bytes,
        config: LoginRateLimitConfig | None = None,
    ) -> None:
        if not callable(getattr(store, "consume", None)):
            raise TypeError("store must implement an atomic consume method.")
        if not isinstance(key_secret, bytes) or len(key_secret) < 32:
            raise ValueError("key_secret must contain at least 32 bytes.")
        if config is None:
            config = LoginRateLimitConfig()
        if type(config) is not LoginRateLimitConfig:
            raise TypeError("config must be LoginRateLimitConfig.")

        self._store = store
        self._key_secret = key_secret
        self._config = config

    def check(self, identifier: str, client_address: str) -> RateLimitDecision:
        """Consume account and address buckets; deny if either limit is reached."""
        normalized_identifier = self._normalize(identifier, "identifier", 254)
        normalized_client = self._normalize(client_address, "client_address", 255)

        identifier_result = self._consume(
            "identifier", normalized_identifier, self._config.identifier_limit
        )
        client_result = self._consume(
            "client", normalized_client, self._config.client_limit
        )
        allowed = identifier_result.allowed and client_result.allowed
        denied = tuple(
            result for result in (identifier_result, client_result) if not result.allowed
        )
        return RateLimitDecision(
            allowed=allowed,
            remaining=min(identifier_result.remaining, client_result.remaining),
            retry_after_seconds=max(
                (result.retry_after_seconds for result in denied), default=0
            ),
        )

    def _consume(self, scope: str, value: str, limit: int) -> RateLimitDecision:
        key = hmac.new(
            self._key_secret,
            f"{scope}\0{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        result = self._store.consume(
            key, limit=limit, window_seconds=self._config.window_seconds
        )
        if type(result) is not RateLimitDecision:
            raise RuntimeError("The rate-limit store returned an invalid decision.")
        return result

    @staticmethod
    def _normalize(value: str, name: str, max_length: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string.")
        normalized = value.strip().casefold() if name == "identifier" else value.strip()
        if not normalized or len(normalized) > max_length:
            raise ValueError(f"{name} has an invalid length.")
        return normalized


def install_login_rate_limiter(
    app,
    store: RateLimitStore,
    key_secret: bytes,
    config: LoginRateLimitConfig | None = None,
) -> LoginRateLimiter:
    """Install the prepared limiter with an explicitly supplied shared store.

    Production stores must be durable and shared across workers. There is no
    local-memory fallback, and the key secret is never read from a default.
    """
    if (
        app.config.get("APP_ENV") == "production"
        and getattr(store, "is_persistent", False) is not True
    ):
        raise ValueError("Production rate limiting requires persistent storage.")
    limiter = LoginRateLimiter(store, key_secret, config)
    app.extensions[LOGIN_RATE_LIMIT_EXTENSION] = limiter
    return limiter


__all__ = [
    "LoginRateLimitConfig",
    "LoginRateLimiter",
    "LOGIN_RATE_LIMIT_EXTENSION",
    "RateLimitDecision",
    "RateLimitStore",
    "install_login_rate_limiter",
]
