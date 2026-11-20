"""Candidate supplier persistence ports; no production installation."""
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping, Protocol

from session_auth import AuthenticatedSession
from branchwise.suppliers.supplier_validation import validate_supplier_input


SUPPLIER_FIELD_LIMITS: Mapping[str, int] = MappingProxyType({
    "name": 160, "contact_person": 160, "phone": 30, "email": 254, "address": 255,
})


class SupplierValidationError(ValueError):
    """Safe field errors; never includes submitted values."""

    def __init__(self, errors: Mapping[str, str]):
        super().__init__("Invalid supplier fields.")
        self.errors = MappingProxyType(dict(errors))


@dataclass(frozen=True)
class SupplierCreateData:
    """Exact supported fields, without tenant identity or lifecycle controls."""

    name: str
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None

    def __post_init__(self):
        values = vars(self)
        errors = validate_supplier_input(values)
        for field, limit in SUPPLIER_FIELD_LIMITS.items():
            value = values[field]
            if isinstance(value, str) and len(value) > limit:
                errors[field] = f"Must be at most {limit} characters."
        for field, value in values.items():
            if isinstance(value, str):
                try:
                    encoded = value.encode("utf-8")
                except UnicodeEncodeError:
                    errors[field] = "Must be valid Unicode text."
                    continue
                if field == "notes" and len(encoded) > 65535:
                    errors[field] = "Must be at most 65535 UTF-8 bytes."
        if errors:
            raise SupplierValidationError(errors)


@dataclass(frozen=True)
class SupplierRecord:
    """Detached persisted data, never an ORM object or SQL exception."""

    supplier_id: int
    business_id: int
    name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierRepository(Protocol):
    """Candidate port using Developer 1's atomic repository conventions.

    Each call authorizes a server-resolved actor and derives the tenant from
    actor.principal. Creation owns a fresh session/transaction and returns only
    after commit. Lookup uses both business_id and supplier_id, treating absent
    and foreign IDs identically. No caller transaction is accepted.
    """

    def create_supplier_atomic(
        self, *, actor: AuthenticatedSession, data: SupplierCreateData
    ) -> SupplierRecord: ...

    def get_supplier(
        self, *, actor: AuthenticatedSession, supplier_id: int
    ) -> SupplierRecord: ...
