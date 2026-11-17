"""Password hashing helpers for the BranchWise backend."""

from werkzeug.security import check_password_hash, generate_password_hash


def hash_password(password: str) -> str:
    """Return an explicit Werkzeug scrypt hash of a plain-text password."""
    return generate_password_hash(password, method="scrypt")


def verify_password(password: str, password_hash: str) -> bool:
    """Check whether a plain-text password matches its stored hash."""
    return check_password_hash(password_hash, password)
