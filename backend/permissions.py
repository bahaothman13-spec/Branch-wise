"""Role permissions and business/branch scope checks for BranchWise.

This module does not authenticate users or load data. A future authentication
layer must build ``AuthenticatedPrincipal`` from verified identity and trusted
stored user data, and build ``ResourceScope`` from a resource loaded by the
server. Never use role, business, branch, or permission values from a client
request as trusted context.
"""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import FrozenSet, Mapping


class Role(str, Enum):
    """Roles currently supported by BranchWise."""

    OWNER = "Owner"
    BRANCH_MANAGER = "Branch Manager"
    EMPLOYEE = "Employee"


# Keep these public permission identifiers stable for existing callers.
PRODUCT_CATALOG_APPROVAL = "product_catalog:approve"
SALE_CANCELLATION_APPROVAL = "sale_cancellation:approve"

DASHBOARD_VIEW = "dashboard:view"
PRODUCTS_VIEW = "products:view"
PRODUCT_CATALOG_MANAGE = "product_catalog:manage"
PRODUCT_REQUEST_SUBMIT = "product_request:submit"
PRODUCT_REQUEST_REVIEW = "product_request:review"
PRODUCT_REQUEST_COMMENT = "product_request:comment"
INVENTORY_VIEW = "inventory:view"
INVENTORY_ADJUST = "inventory:adjust"
SALES_RECORD = "sales:record"
SALES_VIEW = "sales:view"
SALES_VIEW_AUTHORIZED = "sales:view_authorized"
SALE_CANCELLATION_REQUEST = "sale_cancellation:request"
USERS_MANAGE = "users:manage"
BRANCHES_MANAGE = "branches:manage"
BUSINESS_SETTINGS_MANAGE = "business_settings:manage"
SUPPLIERS_VIEW = "suppliers:view"
SUPPLIERS_MANAGE = "suppliers:manage"
PURCHASES_MANAGE = "purchases:manage"
PURCHASES_MANAGE_AUTHORIZED = "purchases:manage_authorized"
TRANSFERS_REQUEST = "transfers:request"
TRANSFERS_APPROVE_SOURCE = "transfers:approve_source"
TRANSFERS_DISPATCH = "transfers:dispatch"
TRANSFERS_CONFIRM_RECEIPT = "transfers:confirm_receipt"
RETURNS_RECORD = "returns:record"
RETURNS_INSPECT = "returns:inspect"
REPORTS_VIEW_BUSINESS = "reports:view_business"
REPORTS_VIEW_BRANCH = "reports:view_branch"
AI_RECOMMENDATIONS = "ai:recommendations"
AI_ASSISTANT = "ai:assistant"
AI_DECISION_LOG = "ai:decision_log"
PASSWORD_CHANGE_SELF = "password:change_self"


_OWNER_PERMISSIONS = frozenset(
    {
        DASHBOARD_VIEW,
        PRODUCTS_VIEW,
        PRODUCT_CATALOG_MANAGE,
        PRODUCT_CATALOG_APPROVAL,
        INVENTORY_VIEW,
        INVENTORY_ADJUST,
        SALES_RECORD,
        SALES_VIEW,
        SALE_CANCELLATION_APPROVAL,
        USERS_MANAGE,
        BRANCHES_MANAGE,
        BUSINESS_SETTINGS_MANAGE,
        SUPPLIERS_VIEW,
        SUPPLIERS_MANAGE,
        PURCHASES_MANAGE,
        TRANSFERS_REQUEST,
        TRANSFERS_APPROVE_SOURCE,
        TRANSFERS_DISPATCH,
        TRANSFERS_CONFIRM_RECEIPT,
        RETURNS_RECORD,
        RETURNS_INSPECT,
        REPORTS_VIEW_BUSINESS,
        AI_RECOMMENDATIONS,
        AI_ASSISTANT,
        AI_DECISION_LOG,
        PASSWORD_CHANGE_SELF,
    }
)

_BRANCH_MANAGER_PERMISSIONS = frozenset(
    {
        DASHBOARD_VIEW,
        PRODUCTS_VIEW,
        PRODUCT_REQUEST_REVIEW,
        PRODUCT_REQUEST_COMMENT,
        INVENTORY_VIEW,
        INVENTORY_ADJUST,
        SALES_RECORD,
        SALES_VIEW,
        SALE_CANCELLATION_APPROVAL,
        SUPPLIERS_VIEW,
        PURCHASES_MANAGE_AUTHORIZED,
        TRANSFERS_REQUEST,
        TRANSFERS_APPROVE_SOURCE,
        TRANSFERS_DISPATCH,
        TRANSFERS_CONFIRM_RECEIPT,
        RETURNS_RECORD,
        RETURNS_INSPECT,
        REPORTS_VIEW_BRANCH,
        AI_RECOMMENDATIONS,
        AI_ASSISTANT,
        AI_DECISION_LOG,
        PASSWORD_CHANGE_SELF,
    }
)

_EMPLOYEE_PERMISSIONS = frozenset(
    {
        DASHBOARD_VIEW,
        PRODUCTS_VIEW,
        INVENTORY_VIEW,
        SALES_RECORD,
        SALES_VIEW_AUTHORIZED,
        PRODUCT_REQUEST_SUBMIT,
        SALE_CANCELLATION_REQUEST,
        RETURNS_RECORD,
        PASSWORD_CHANGE_SELF,
    }
)

# The policy is immutable so application code cannot broaden it at runtime.
ROLE_PERMISSIONS: Mapping[Role, FrozenSet[str]] = MappingProxyType(
    {
        Role.OWNER: _OWNER_PERMISSIONS,
        Role.BRANCH_MANAGER: _BRANCH_MANAGER_PERMISSIONS,
        Role.EMPLOYEE: _EMPLOYEE_PERMISSIONS,
    }
)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Trusted user data prepared by the future authentication layer.

    ``permissions`` is a trusted server-side grant set. It may narrow the
    permissions available to a role, but ``has_permission`` never lets it
    grant an operation outside ``ROLE_PERMISSIONS``. Owners have no single
    assigned branch; managers and employees must have one.
    """

    role: Role
    business_id: int
    branch_id: int | None
    permissions: FrozenSet[str]


@dataclass(frozen=True)
class ResourceScope:
    """Business and optional branch scope of a server-loaded resource."""

    business_id: int
    branch_id: int | None


def has_permission(
    principal: AuthenticatedPrincipal,
    required_permission: str,
    resource: ResourceScope,
) -> bool:
    """Check the role matrix, trusted grants, and business/branch scope.

    Owners may access any resource in their own business. Branch managers and
    employees need a matching assigned branch. A branch-scoped operation must
    pass the relevant branch in ``resource`` (for example, a transfer dispatch
    uses its source branch and receipt confirmation uses its destination).

    This function only checks the trusted context it receives. It is not an
    authentication mechanism and must not receive client-provided identity,
    role, permission, or resource-scope values.
    """

    if type(principal.role) is not Role:
        return False
    if not isinstance(required_permission, str):
        return False

    role_permissions = ROLE_PERMISSIONS.get(principal.role)
    if role_permissions is None or required_permission not in role_permissions:
        return False

    if required_permission not in principal.permissions:
        return False

    if principal.business_id is None or resource.business_id is None:
        return False

    if principal.business_id != resource.business_id:
        return False

    if principal.role is Role.OWNER:
        return True

    return (
        principal.branch_id is not None
        and resource.branch_id is not None
        and principal.branch_id == resource.branch_id
    )
