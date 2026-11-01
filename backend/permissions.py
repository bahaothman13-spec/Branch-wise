"""Small, reusable permission checks for BranchWise API operations.

This module does not authenticate users or load data. Future API code must build
``AuthenticatedPrincipal`` from a verified login and trusted stored user data,
and build ``ResourceScope`` from the target resource loaded by the server.
Never build either object from role, business_id, or branch_id values supplied
by the client. The requested permission should also be a server-defined value.

Employee operation grants are deliberately not defined here. The team must
decide which operations employees may perform before assigning any grants.
Until those decisions are made, an empty ``permissions`` set denies access.
"""

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class Role(str, Enum):
    """Roles currently supported by BranchWise."""

    OWNER = "Owner"
    BRANCH_MANAGER = "Branch Manager"
    EMPLOYEE = "Employee"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Trusted user data prepared by the future authentication layer.

    ``permissions`` must come from server-side policy or stored data, never
    directly from a request. Owners have no single assigned branch; managers
    and employees must have one.
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
    """Return whether a user is granted an operation on a resource in scope.

    The scope rules are:

    * All roles need an explicit grant for ``required_permission``.
    * Owners may access resources in their own business across its branches.
    * Branch managers and employees are limited to their assigned branch.

    Missing business or branch scope fails closed. This function only checks
    permission data it receives; it must not be used with client-supplied role
    or scope values as evidence of identity or authorization.
    """

    if principal.role not in (Role.OWNER, Role.BRANCH_MANAGER, Role.EMPLOYEE):
        return False

    if not required_permission or required_permission not in principal.permissions:
        return False

    if principal.business_id is None or resource.business_id is None:
        return False

    if principal.business_id != resource.business_id:
        return False

    if principal.role == Role.OWNER:
        return True

    if principal.role in (Role.BRANCH_MANAGER, Role.EMPLOYEE):
        return (
            principal.branch_id is not None
            and resource.branch_id is not None
            and principal.branch_id == resource.branch_id
        )

    return False


# Before enabling Employee operations, the team must decide:
# 1. Which operation names Employees may receive (the list is intentionally empty here).
# 2. Whether grants are shared by all Employees or assigned per Employee.
# 3. Whether permissions need finer distinctions, such as viewing versus editing.
