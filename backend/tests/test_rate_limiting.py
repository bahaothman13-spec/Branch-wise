"""Tests for the injectable login rate-limit foundation."""

import pytest
from app import create_app

from rate_limiting import (
    LoginRateLimitConfig,
    LoginRateLimiter,
    RateLimitDecision,
    install_login_rate_limiter,
)


class FakeRateLimitStore:
    """Small local test double; no in-memory store ships as an implementation."""

    def __init__(self):
        self.counts = {}
        self.calls = []

    def consume(self, key, *, limit, window_seconds):
        self.calls.append((key, limit, window_seconds))
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        allowed = count <= limit
        return RateLimitDecision(
            allowed=allowed,
            remaining=max(limit - count, 0),
            retry_after_seconds=window_seconds if not allowed else 0,
        )


def test_login_rate_limiter_enforces_account_and_client_rules():
    store = FakeRateLimitStore()
    limiter = LoginRateLimiter(
        store,
        b"k" * 32,
        LoginRateLimitConfig(
            identifier_limit=2,
            client_limit=4,
            window_seconds=60,
        ),
    )

    assert limiter.check("Owner@Example.com", "192.0.2.10").allowed
    assert limiter.check("owner@example.com", "192.0.2.11").allowed
    denied = limiter.check("OWNER@example.com", "192.0.2.12")

    assert not denied.allowed
    assert denied.retry_after_seconds == 60
    assert len(store.calls) == 6
    assert {call[1:] for call in store.calls} == {(2, 60), (4, 60)}


def test_login_rate_limiter_uses_opaque_account_and_client_bucket_keys():
    store = FakeRateLimitStore()
    limiter = LoginRateLimiter(store, b"s" * 32)

    limiter.check("private@example.com", "203.0.113.8")

    keys = [key for key, _limit, _window in store.calls]
    assert len(keys) == 2
    assert all(len(key) == 64 for key in keys)
    assert len(set(keys)) == 2
    assert "private@example.com" not in repr(keys)
    assert "203.0.113.8" not in repr(keys)
    assert "s" * 32 not in repr(limiter)


def test_blocked_client_does_not_create_further_identifier_buckets():
    store = FakeRateLimitStore()
    limiter = LoginRateLimiter(
        store,
        b"k" * 32,
        LoginRateLimitConfig(identifier_limit=5, client_limit=1, window_seconds=60),
    )

    assert limiter.check("first@example.com", "203.0.113.8").allowed
    denied = limiter.check("second@example.com", "203.0.113.8")

    assert denied.allowed is False
    assert len(store.calls) == 3


@pytest.mark.parametrize("missing_marker", ["is_shared", "is_atomic"])
def test_production_limiter_rejects_persistent_but_unsafe_stores(missing_marker):
    app = create_app(
        {
            "APP_ENV": "production",
            "SESSION_COOKIE_SECURE": True,
            "DEBUG": False,
            "SECRET_KEY": "k" * 32,
            "SQLALCHEMY_DATABASE_URI": "mysql+mysqlconnector://test:test@db.test/branchwise",
        }
    )
    store = FakeRateLimitStore()
    store.is_persistent = True
    store.is_shared = True
    store.is_atomic = True
    setattr(store, missing_marker, False)

    with pytest.raises(ValueError, match="shared atomic counters"):
        install_login_rate_limiter(app, store, b"k" * 32)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"identifier_limit": 0},
        {"identifier_limit": True},
        {"client_limit": -1},
        {"client_limit": 10001},
        {"window_seconds": 0},
        {"window_seconds": 86401},
    ],
)
def test_invalid_login_limit_rules_are_rejected(kwargs):
    with pytest.raises(ValueError):
        LoginRateLimitConfig(**kwargs)


@pytest.mark.parametrize("secret", [None, "a" * 32, b"short"])
def test_limiter_requires_a_separate_sufficient_key_secret(secret):
    with pytest.raises(ValueError, match="key_secret"):
        LoginRateLimiter(FakeRateLimitStore(), secret)


def test_limiter_requires_explicit_store_interface():
    with pytest.raises(TypeError, match="store"):
        LoginRateLimiter(None, b"k" * 32)


@pytest.mark.parametrize(
    ("identifier", "client_address"),
    [("  ", "192.0.2.1"), ("a" * 255, "192.0.2.1"), ("user@example.com", "")],
)
def test_limiter_rejects_invalid_bucket_inputs(identifier, client_address):
    limiter = LoginRateLimiter(FakeRateLimitStore(), b"k" * 32)

    with pytest.raises(ValueError):
        limiter.check(identifier, client_address)
