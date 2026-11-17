"""Internal BE-29 input preparation; no HTTP, lookups or persistence."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from isolation_rules import (
    ACCESS_DENIED_MESSAGE,
    is_valid_trusted_context,
    require_resource_access,
)
from onboarding_rules import is_branch_operation_ready
from permissions import PURCHASES_MANAGE, PURCHASES_MANAGE_AUTHORIZED, ResourceScope, Role
from request_validation import is_positive_integer
from session_auth import AuthenticatedSession
from branchwise.purchasing.purchase_order_validation import (
    ALLOWED_FIELDS,
    ALLOWED_ITEM_FIELDS,
    validate_purchase_order_input,
)


@dataclass(frozen=True)
class PurchaseOrderPreparation:
    """Preliminary errors only; empty errors are never a persistence command.

    Supplier/catalog existence, eligibility and authoritative prices remain
    unchecked. This result does not establish production persistence readiness.
    """
    errors: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", MappingProxyType(dict(self.errors)))


def prepare_purchase_order_input(
    data,
    *,
    actor: AuthenticatedSession,
    branch_scope: ResourceScope,
    business_branch_count: int,
) -> PurchaseOrderPreparation:
    """Authorize trusted context, then validate the caller's unchanged input.

    The caller must supply a session resolved by Developer 1's SessionManager,
    the destination scope established by the server, and a branch count loaded
    for that business. None may be built from request metadata. Session expiry
    and current-account checks belong to the session manager; future HTTP
    callers must also use the established session/CSRF decorator.

    Authorization failures raise the shared non-disclosing PermissionError.
    Invalid trusted branch counts retain the onboarding helper's ValueError;
    missing trusted arguments are rejected by the keyword-only interface.
    Structural errors are returned without normalizing or copying input into
    an order. No references or prices are verified, and no identity, totals,
    status, database writes or inventory changes are produced.
    """
    if (
        type(actor) is not AuthenticatedSession
        or not is_positive_integer(actor.user_id)
        or not is_valid_trusted_context(actor.principal, branch_scope)
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    permission = (
        PURCHASES_MANAGE
        if actor.principal.role is Role.OWNER
        else PURCHASES_MANAGE_AUTHORIZED
    )
    require_resource_access(actor.principal, permission, branch_scope)
    if not is_branch_operation_ready(
        actor.principal, permission, branch_scope, business_branch_count
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    # Malformed/missing IDs remain structural errors from the existing validator.
    if (
        isinstance(data, dict)
        and is_positive_integer(data.get("branch_id"))
        and data["branch_id"] != branch_scope.branch_id
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)

    errors = validate_purchase_order_input(data)
    if isinstance(data, dict):
        if set(data) - ALLOWED_FIELDS:
            errors["fields"] = "Unknown fields are not allowed."
        items = data.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict) and set(item) - ALLOWED_ITEM_FIELDS:
                    errors[f"items[{index}].fields"] = "Unknown fields are not allowed."

    return PurchaseOrderPreparation(errors)
