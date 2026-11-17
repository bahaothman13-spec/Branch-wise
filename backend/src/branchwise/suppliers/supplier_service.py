"""Internal supplier preparation; no HTTP or persistence operations."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from isolation_rules import ACCESS_DENIED_MESSAGE, require_resource_access
from permissions import ResourceScope, SUPPLIERS_MANAGE
from request_validation import is_positive_integer, validate_allowed_fields
from session_auth import AuthenticatedSession
from branchwise.suppliers.supplier_validation import OPTIONAL_FIELDS, validate_supplier_input


@dataclass(frozen=True)
class SupplierPreparation:
    """Internal errors only; empty errors never mean a supplier was saved."""
    errors: Mapping[str, str]


def prepare_supplier_create(data, *, actor: AuthenticatedSession, scope: ResourceScope) -> SupplierPreparation:
    """Check trusted server context before validating unchanged supplier input.

    Caller must supply a session resolved by Developer 1's session manager and
    a server-established scope, never request metadata. Session resolution owns
    expiry/current account checks; HTTP callers must also use the existing CSRF
    decorator. This function neither authenticates nor re-resolves sessions.
    Access denial raises the shared PermissionError; validation returns field
    errors. No normalized payload, record identity or persistence claim is made.
    """
    if type(actor) is not AuthenticatedSession or not is_positive_integer(actor.user_id):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    require_resource_access(actor.principal, SUPPLIERS_MANAGE, scope)
    errors = validate_supplier_input(data)
    if isinstance(data, dict):
        errors.update(validate_allowed_fields(data, ("name", *OPTIONAL_FIELDS)))
    return SupplierPreparation(MappingProxyType(errors))
