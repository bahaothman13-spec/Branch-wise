"""Internal BE-30 input preparation; no receiving, lookups or persistence."""

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
from branchwise.purchasing.purchase_receiving_validation import (
    ALLOWED_FIELDS,
    ALLOWED_ITEM_FIELDS,
    validate_purchase_receiving_input,
)


@dataclass(frozen=True)
class PurchaseReceivingPreparation:
    """Preliminary errors only; empty errors never authorize receipt execution.

    Order existence, ownership, destination, line membership, receivable
    quantities and receiving eligibility remain unchecked. This result makes
    no receiving, inventory, transaction or durable idempotency claim.
    """

    errors: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", MappingProxyType(dict(self.errors)))


def prepare_purchase_receiving_input(
    data,
    *,
    actor: AuthenticatedSession,
    branch_scope: ResourceScope,
    business_branch_count: int,
) -> PurchaseReceivingPreparation:
    """Authorize trusted context, then validate the caller's unchanged input.

    The caller must supply a session resolved by Developer 1's SessionManager,
    a destination scope established by the server, and a branch count loaded
    for that business. None may be built from request metadata. Session expiry
    and current-account checks belong to the session manager; future HTTP
    callers must also use the established session/CSRF decorator.

    Checking the supplied destination scope does not verify that the submitted
    purchase order belongs to that business or destination. No order or line
    is loaded, no remaining quantity or lifecycle policy is applied, and no
    receipt identity, timestamp, movement, status or persistent command is made.

    Authorization failures raise the shared non-disclosing PermissionError.
    Invalid trusted branch counts retain the onboarding helper's ValueError;
    missing trusted arguments are rejected by the keyword-only interface.
    Empty errors mean preliminary authorization and structural checks only.
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

    errors = validate_purchase_receiving_input(data)
    if isinstance(data, dict):
        if set(data) - ALLOWED_FIELDS:
            errors["fields"] = "Unknown fields are not allowed."
        items = data.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict) and set(item) - ALLOWED_ITEM_FIELDS:
                    errors[f"items[{index}].fields"] = "Unknown fields are not allowed."

    return PurchaseReceivingPreparation(errors)
