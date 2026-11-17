"""Pure business rules for first-branch onboarding.

The caller must build the principal and resource scope from authenticated,
server-side data. ``branch_count`` must be loaded for that principal's business.
These helpers do not create branches, authenticate users, or access a database.
"""

from dataclasses import dataclass

from permissions import (
    BRANCHES_MANAGE,
    BUSINESS_SETTINGS_MANAGE,
    INVENTORY_ADJUST,
    INVENTORY_VIEW,
    PASSWORD_CHANGE_SELF,
    PRODUCT_REQUEST_COMMENT,
    PRODUCT_REQUEST_REVIEW,
    PRODUCT_REQUEST_SUBMIT,
    PURCHASES_MANAGE,
    PURCHASES_MANAGE_AUTHORIZED,
    REPORTS_VIEW_BRANCH,
    RETURNS_INSPECT,
    RETURNS_RECORD,
    SALE_CANCELLATION_APPROVAL,
    SALE_CANCELLATION_REQUEST,
    SALES_RECORD,
    SALES_VIEW,
    SALES_VIEW_AUTHORIZED,
    TRANSFERS_APPROVE_SOURCE,
    TRANSFERS_CONFIRM_RECEIPT,
    TRANSFERS_DISPATCH,
    TRANSFERS_REQUEST,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    has_permission,
)
from request_validation import is_positive_integer


AUTHENTICATION = "authentication"
LOGOUT = "logout"

ONBOARDING_REQUIRED_MESSAGE = (
    "Create the first branch for your business to complete onboarding."
)

ONBOARDING_ALLOWED_PERMISSIONS = frozenset(
    {BRANCHES_MANAGE, BUSINESS_SETTINGS_MANAGE, PASSWORD_CHANGE_SELF}
)

BRANCH_DEPENDENT_PERMISSIONS = frozenset(
    {
        INVENTORY_VIEW,
        INVENTORY_ADJUST,
        SALES_RECORD,
        SALES_VIEW,
        SALES_VIEW_AUTHORIZED,
        SALE_CANCELLATION_APPROVAL,
        SALE_CANCELLATION_REQUEST,
        PURCHASES_MANAGE,
        PURCHASES_MANAGE_AUTHORIZED,
        TRANSFERS_REQUEST,
        TRANSFERS_APPROVE_SOURCE,
        TRANSFERS_DISPATCH,
        TRANSFERS_CONFIRM_RECEIPT,
        RETURNS_RECORD,
        RETURNS_INSPECT,
        PRODUCT_REQUEST_SUBMIT,
        PRODUCT_REQUEST_REVIEW,
        PRODUCT_REQUEST_COMMENT,
        REPORTS_VIEW_BRANCH,
    }
)


@dataclass(frozen=True)
class OnboardingStatus:
    """Onboarding state returned after checking the trusted branch count."""

    required: bool
    message: str | None = None


def _require_branch_count(branch_count):
    if type(branch_count) is not int or branch_count < 0:
        raise ValueError("branch_count must be a non-negative integer.")


def _valid_principal(principal):
    return (
        isinstance(principal, AuthenticatedPrincipal)
        and type(principal.role) is Role
        and is_positive_integer(principal.business_id)
    )


def _valid_business_id(business_id):
    return is_positive_integer(business_id)


def get_onboarding_status(principal, branch_count):
    """Return whether an Owner must create the first branch after login.

    ``branch_count`` must come from a server-side query scoped to
    ``principal.business_id``. Other roles are never asked to create a branch.
    """
    _require_branch_count(branch_count)
    if not _valid_principal(principal):
        raise ValueError("A trusted authenticated principal is required.")

    required = principal.role is Role.OWNER and branch_count == 0
    return OnboardingStatus(
        required=required,
        message=ONBOARDING_REQUIRED_MESSAGE if required else None,
    )


def can_manage_branches(principal, business_id):
    """Check branch-management permission in a trusted business scope."""
    if not _valid_principal(principal) or not _valid_business_id(business_id):
        return False

    return has_permission(
        principal,
        BRANCHES_MANAGE,
        ResourceScope(business_id=business_id, branch_id=None),
    )


def can_create_first_branch(principal, business_id, branch_count):
    """Allow only the same-business Owner to create a branch when none exist."""
    _require_branch_count(branch_count)
    return branch_count == 0 and can_manage_branches(principal, business_id)


def is_branch_operation_ready(principal, permission, resource, branch_count):
    """Check permission and trusted scope after at least one branch exists.

    The resource must identify a real branch loaded by the server. Passing a
    business or branch ID from a request body is not a trusted authorization
    context.
    """
    _require_branch_count(branch_count)
    if (
        branch_count == 0
        or not isinstance(permission, str)
        or permission not in BRANCH_DEPENDENT_PERMISSIONS
    ):
        return False
    if not _valid_principal(principal) or not isinstance(resource, ResourceScope):
        return False
    if not _valid_business_id(resource.business_id):
        return False
    if not is_positive_integer(resource.branch_id):
        return False

    return has_permission(principal, permission, resource)


def is_operation_allowed_during_onboarding(principal, operation, resource=None):
    """Check access to authentication, logout, and permitted setup operations.

    Authentication and logout are intentionally available without a principal.
    Other allowed operations use the existing role matrix and trusted scope.
    Branch-dependent permission identifiers are never allowed by this helper.
    """
    if not isinstance(operation, str):
        return False
    if operation in {AUTHENTICATION, LOGOUT}:
        return True
    if operation not in ONBOARDING_ALLOWED_PERMISSIONS:
        return False
    if not _valid_principal(principal) or not isinstance(resource, ResourceScope):
        return False
    if not _valid_business_id(resource.business_id):
        return False

    if operation in {BRANCHES_MANAGE, BUSINESS_SETTINGS_MANAGE}:
        if resource.branch_id is not None:
            return False
    elif principal.role is Role.OWNER:
        if resource.branch_id is not None:
            return False
    elif not is_positive_integer(resource.branch_id):
        return False

    return has_permission(principal, operation, resource)
