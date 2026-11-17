"""Small validation helpers shared by the current API routes."""

import re

from flask import request


EMAIL_LOCAL_ATOM = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_LABEL = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


def read_json_object():
    """Return a JSON object or a short message suitable for an API response."""
    if not request.is_json:
        return None, "Request body must be JSON."

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object."

    return data, None


def validate_allowed_fields(data, allowed_fields, message="This field is not allowed."):
    """Reject unknown input keys without reflecting attacker-controlled names."""
    allowed = frozenset(allowed_fields)
    if any(field not in allowed for field in data):
        return {"body": message}
    return {}


def required_string_error(data, field):
    """Return the existing validation message for a required string field."""
    if field not in data:
        return "This field is required."

    value = data[field]
    if not isinstance(value, str):
        return "Must be a string."
    if not value.strip():
        return "This field is required."

    return None


def is_valid_email(value):
    """Check for a non-empty local part and dot-separated domain labels."""
    if not isinstance(value, str):
        return False

    email = value.strip()
    if len(email) > 254 or email.count("@") != 1:
        return False

    local_part, domain = email.split("@")
    if (
        not local_part
        or len(local_part) > 64
        or local_part.startswith(".")
        or local_part.endswith(".")
        or ".." in local_part
    ):
        return False

    if not all(
        EMAIL_LOCAL_ATOM.fullmatch(part)
        for part in local_part.split(".")
    ):
        return False

    domain_labels = domain.split(".")
    return (
        len(domain_labels) >= 2
        and all(EMAIL_DOMAIN_LABEL.fullmatch(label) for label in domain_labels)
    )


def password_length_error(value, minimum_length):
    """Return the password length message when a non-empty string is too short."""
    if (
        isinstance(value, str)
        and value.strip()
        and len(value) < minimum_length
    ):
        return f"Password must be at least {minimum_length} characters long."

    return None


def is_positive_integer(value):
    """Accept positive ints but reject bool, which is an int subclass in Python."""
    return type(value) is int and value > 0


def parse_positive_integer_id(value):
    """Parse a positive decimal path ID, safely rejecting malformed or huge IDs."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        return None

    try:
        numeric_id = int(value)
    except ValueError:
        # Python limits conversion of extremely long decimal strings.
        return None

    return numeric_id if numeric_id > 0 else None
