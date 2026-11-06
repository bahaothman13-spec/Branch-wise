"""Pure business and branch isolation rules for trusted server-side data.

Build principals from authenticated identity and stored user data. Build each
resource scope from a resource loaded and verified by the server. Never build
either value from request-supplied business IDs, branch IDs, roles, or grants.
These helpers do not authenticate users, load resources, or access a database.
"""

from collections.abc import Iterable, Mapping

from permissions import (
    has_permission,
    is_trusted_principal,
    is_valid_resource_scope,
)
from request_validation import is_positive_integer


ACCESS_DENIED_MESSAGE = "Resource not found or access denied."


def is_valid_trusted_context(principal, resource_scope) -> bool:
    """Validate that a trusted user and resource scope are complete and valid."""
    return is_trusted_principal(principal) and is_valid_resource_scope(resource_scope)


def resources_belong_to_business(business_id, *resource_scopes) -> bool:
    """Check that every supplied server-loaded resource belongs to one business.

    This can validate branches, products, variants, and other business-owned
    records before an operation uses them. Scopes may be branchless for
    business-wide resources. At least one resource is required.
    """
    if not is_positive_integer(business_id) or not resource_scopes:
        return False
    return all(
        is_valid_resource_scope(scope) and scope.business_id == business_id
        for scope in resource_scopes
    )


def can_access_resource(
    principal,
    required_permission,
    resource_scope,
    *,
    related_scopes=(),
) -> bool:
    """Check access to one resource and any business-owned dependencies.

    ``resource_scope`` is the scope at which the requested operation occurs,
    such as the branch owning a sale or inventory record. ``related_scopes``
    can include referenced products or variants; all must belong to the same
    business before RBAC and branch checks are applied to the primary scope.
    """
    if not is_valid_trusted_context(principal, resource_scope):
        return False
    if not isinstance(related_scopes, (tuple, list)):
        return False
    if related_scopes and not resources_belong_to_business(
        resource_scope.business_id, *related_scopes
    ):
        return False
    return has_permission(principal, required_permission, resource_scope)


def require_resource_access(
    principal,
    required_permission,
    resource_scope,
    *,
    related_scopes=(),
) -> None:
    """Raise a non-disclosing denial when the resource cannot be accessed."""
    if not can_access_resource(
        principal,
        required_permission,
        resource_scope,
        related_scopes=related_scopes,
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)


def can_access_transfer(
    principal,
    required_permission,
    source_branch,
    destination_branch,
    *,
    permission_branch="both",
    related_scopes=(),
) -> bool:
    """Validate both transfer branches and the permission at its relevant side.

    Both branch scopes must be server-loaded, belong to the actor's business,
    and have distinct valid branch IDs. ``permission_branch`` selects where
    the operation is authorized: use ``"source"`` for source-side actions
    such as request or dispatch, ``"destination"`` for receipt confirmation,
    or ``"both"`` when the actor must be allowed to act at each branch.
    """
    if not is_trusted_principal(principal):
        return False
    if not is_valid_resource_scope(source_branch) or not is_valid_resource_scope(
        destination_branch
    ):
        return False
    if (
        not is_positive_integer(source_branch.branch_id)
        or not is_positive_integer(destination_branch.branch_id)
        or source_branch.branch_id == destination_branch.branch_id
        or source_branch.business_id != destination_branch.business_id
        or source_branch.business_id != principal.business_id
    ):
        return False
    if not isinstance(related_scopes, (tuple, list)):
        return False
    if related_scopes and not resources_belong_to_business(
        principal.business_id, *related_scopes
    ):
        return False

    if permission_branch == "source":
        scopes_to_authorize = (source_branch,)
    elif permission_branch == "destination":
        scopes_to_authorize = (destination_branch,)
    elif permission_branch == "both":
        scopes_to_authorize = (source_branch, destination_branch)
    else:
        return False

    return all(
        has_permission(principal, required_permission, scope)
        for scope in scopes_to_authorize
    )


def filter_accessible_resources(
    principal,
    required_permission,
    resources,
    *,
    scope_getter,
    related_scopes_getter=None,
) -> tuple:
    """Return only permitted resources, silently omitting invalid or foreign rows.

    The caller provides accessors for trusted server-loaded resource objects.
    A failed accessor, invalid scope, foreign dependency, or denied permission
    removes that item from the result so collection APIs do not reveal its
    existence. Results preserve input order and are returned as an immutable
    tuple.
    """
    if (
        not is_trusted_principal(principal)
        or not callable(scope_getter)
        or (related_scopes_getter is not None and not callable(related_scopes_getter))
        or isinstance(resources, (str, bytes, Mapping))
        or not isinstance(resources, Iterable)
    ):
        return ()

    permitted = []
    try:
        iterator = iter(resources)
        for item in iterator:
            try:
                scope = scope_getter(item)
                related_scopes = (
                    () if related_scopes_getter is None else related_scopes_getter(item)
                )
                if can_access_resource(
                    principal,
                    required_permission,
                    scope,
                    related_scopes=related_scopes,
                ):
                    permitted.append(item)
            except Exception:
                # Invalid or unreadable rows are omitted, never exposed.
                continue
    except Exception:
        return ()
    return tuple(permitted)


__all__ = [
    "ACCESS_DENIED_MESSAGE",
    "can_access_resource",
    "can_access_transfer",
    "filter_accessible_resources",
    "is_valid_trusted_context",
    "require_resource_access",
    "resources_belong_to_business",
]
