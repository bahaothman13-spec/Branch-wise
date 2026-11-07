"""Unit tests for password hashing and verification."""

from security import hash_password, verify_password
from werkzeug.security import generate_password_hash


def test_hash_password_does_not_return_the_plain_text_password():
    password = "correct horse battery staple"

    password_hash = hash_password(password)

    assert password_hash != password


def test_hash_password_explicitly_uses_scrypt():
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("scrypt:")


def test_hash_password_uses_a_different_salt_for_each_hash():
    password = "correct horse battery staple"

    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash != second_hash


def test_verify_password_accepts_the_matching_password():
    password = "correct horse battery staple"
    password_hash = hash_password(password)

    assert verify_password(password, password_hash)


def test_verify_password_rejects_a_different_password():
    password_hash = hash_password("correct horse battery staple")

    assert not verify_password("different password", password_hash)


def test_verify_password_keeps_accepting_existing_werkzeug_default_hashes():
    password = "existing password"
    # This is how the old helper called Werkzeug before it selected a method.
    existing_hash = generate_password_hash(password)

    assert verify_password(password, existing_hash)
