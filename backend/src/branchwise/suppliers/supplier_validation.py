"""Database-independent checks for the six supplier fields in BE-28.

These dictionary labels and field errors are internal helper details, not a
final API contract or database schema. Unknown keys are left to a future API
contract; this helper must not be used as a whitelist for saving request data.

Optional omissions, None, and blank strings mean no information was supplied
for these checks. This does not decide database nullability or how to store
empty values. Input values are never changed or normalized here.
"""

import re


OPTIONAL_FIELDS = ("contact_person", "phone", "email", "address", "notes")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def validate_supplier_input(data):
    """Return field errors without authentication, networking, or persistence.

    Only name is required. Populated optional values must be strings. Email
    receives a basic syntax check after trimming surrounding whitespace for
    the check only; it is not a full email-standard or deliverability check.
    Phone validation is type-only until a format policy is approved.

    Duplicate names, business ownership, field lengths, and deactivation rules
    require later integration and are intentionally not checked here.
    """
    if not isinstance(data, dict):
        return {"body": "Supplier input must be a dictionary."}

    errors = {}
    name = data.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        errors["name"] = "Supplier name is required."
    elif not isinstance(name, str):
        errors["name"] = "Must be a string."

    for field in OPTIONAL_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            errors[field] = "Must be a string."

    email = data.get("email")
    if isinstance(email, str) and email.strip():
        if EMAIL_PATTERN.fullmatch(email.strip()) is None:
            errors["email"] = "Enter a valid email address."

    return errors
