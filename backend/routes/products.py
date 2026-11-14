"""Authenticated APIs for the shared, business-wide product catalog."""

from decimal import Decimal, InvalidOperation
import re

from flask import Blueprint, current_app, g, request

from isolation_rules import can_access_resource
from permissions import (
    PRODUCT_CATALOG_MANAGE,
    PRODUCTS_VIEW,
    ResourceScope,
    Role,
    is_trusted_principal,
    is_valid_resource_scope,
)
from product_rules import (
    PRODUCT_CODE_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    CatalogCodeEntry,
    ProductCategoryContext,
    ProductData,
    ProductStatusProposal,
    require_product_catalog_access,
    validate_product_activation,
    validate_product_creation,
    validate_product_deactivation,
    validate_product_modification,
)
from repository_contracts import (
    PRODUCT_REPOSITORY_EXTENSION,
    ProductListResult,
    ProductRecord,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import (
    is_positive_integer,
    parse_positive_integer_id,
    read_json_object,
    required_string_error,
    validate_allowed_fields,
)
from response_helpers import error_response, json_response
from session_auth import require_authenticated_session


products_bp = Blueprint("products", __name__, url_prefix="/api/products")

_CREATE_FIELDS = ("name", "product_code", "category_id", "selling_price", "cost_price")
_UPDATE_FIELDS = _CREATE_FIELDS
_STATUS_FIELDS = ("is_active",)
_LIST_FILTERS = frozenset({"q", "category_id", "is_active", "page", "per_page"})
_PAGE_DEFAULT = 1
_PER_PAGE_DEFAULT = 20
_PER_PAGE_MAX = 100
_PAGE_MAX = 1_000_000
_DECIMAL_TEXT = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)


def _catalog_view_allowed(actor):
    principal = actor.principal
    if not is_trusted_principal(principal):
        return False
    # Product catalog data is shared across a business. For branch roles the
    # actor's assigned branch is used only to validate their trusted grant.
    scope = ResourceScope(
        business_id=principal.business_id,
        branch_id=(None if principal.role is Role.OWNER else principal.branch_id),
    )
    return can_access_resource(principal, PRODUCTS_VIEW, scope)


def _catalog_manage_allowed(actor):
    principal = actor.principal
    if not is_trusted_principal(principal):
        return False
    return can_access_resource(
        principal,
        PRODUCT_CATALOG_MANAGE,
        ResourceScope(business_id=principal.business_id, branch_id=None),
    )


def _repository():
    return current_app.extensions.get(PRODUCT_REPOSITORY_EXTENSION)


def _repository_error(error):
    if isinstance(error, RepositoryAuthorizationError):
        return error_response("Permission denied.", status_code=403)
    if isinstance(error, RepositoryNotFoundError):
        return error_response("Resource not found.", status_code=404)
    if isinstance(error, RepositoryConflictError):
        return error_response(
            "The request conflicts with the current resource state.", status_code=409
        )
    return error_response(
        "The product operation could not be completed.", status_code=500
    )


def _category_or_error(repository, actor, category_id):
    category = repository.get_category(actor=actor, category_id=category_id)
    if type(category) is not ProductCategoryContext:
        raise RepositoryError("The repository returned invalid category data.")
    if (
        type(category.category_id) is not int
        or category.category_id != category_id
        or not is_valid_resource_scope(category.scope)
        or type(category.is_active) is not bool
        or category.scope.business_id != actor.principal.business_id
        or category.scope.branch_id is not None
    ):
        raise RepositoryNotFoundError("Category is outside the actor's business.")
    return category


def _catalog_codes(repository, actor):
    catalog_codes = repository.list_catalog_codes(actor=actor)
    if type(catalog_codes) not in (tuple, list) or any(
        type(entry) is not CatalogCodeEntry
        or entry.business_id != actor.principal.business_id
        or not isinstance(entry.code, str)
        or not entry.code.strip()
        or not isinstance(entry.resource_type, str)
        or entry.resource_type not in {"product", "variant"}
        or type(entry.resource_id) is not int
        or entry.resource_id <= 0
        for entry in catalog_codes
    ):
        raise RepositoryError("The repository returned invalid catalog codes.")
    return catalog_codes


def _record_or_error(record, actor, *, product_id=None):
    if type(record) is not ProductRecord:
        raise RepositoryError("The repository returned invalid product data.")
    if (
        record.business_id != actor.principal.business_id
        or (product_id is not None and record.product_id != product_id)
    ):
        raise RepositoryNotFoundError("Product is outside the actor's business.")
    return record


def _product_json(record):
    data = record.data
    return {
        "product_id": record.product_id,
        "name": data.name,
        "product_code": data.product_code,
        "category_id": data.category_id,
        "selling_price": format(data.selling_price, ".3f"),
        "cost_price": format(data.cost_price, ".3f"),
        "is_active": record.is_active,
    }


def _product_body_errors(data, *, partial):
    fields = _UPDATE_FIELDS if partial else _CREATE_FIELDS
    errors = validate_allowed_fields(data, fields)
    if partial and not data:
        errors["body"] = "Provide at least one product field to update."

    for field_name, max_length in (
        ("name", PRODUCT_NAME_MAX_LENGTH),
        ("product_code", PRODUCT_CODE_MAX_LENGTH),
    ):
        if field_name not in data:
            if not partial:
                errors[field_name] = "This field is required."
            continue
        field_error = required_string_error(data, field_name)
        if field_error:
            errors[field_name] = field_error
        elif len(data[field_name].strip()) > max_length:
            errors[field_name] = f"Must be at most {max_length} characters."

    if "category_id" not in data:
        if not partial:
            errors["category_id"] = "This field is required."
    elif not is_positive_integer(data["category_id"]):
        errors["category_id"] = "Must be a positive integer."

    for field_name in ("selling_price", "cost_price"):
        if field_name not in data:
            if field_name == "selling_price" and not partial:
                errors[field_name] = "This field is required."
            continue
        value = data[field_name]
        if isinstance(value, bool) or isinstance(value, float) or not isinstance(
            value, (int, str)
        ):
            errors[field_name] = "Must be an exact decimal string or integer."
        elif isinstance(value, str) and (
            not value.strip() or _DECIMAL_TEXT.fullmatch(value.strip()) is None
        ):
            errors[field_name] = "Must be a valid decimal value."
        elif isinstance(value, str):
            try:
                amount = Decimal(value.strip())
            except InvalidOperation:
                errors[field_name] = "Must be a valid decimal value."
            else:
                if not amount.is_finite():
                    errors[field_name] = "Must be a finite decimal value."
    return errors


def _field_for_rule_error(message):
    if "already used" in message:
        return "product_code"
    if "category_id" in message or "category" in message:
        return "category_id"
    if "selling_price" in message:
        return "selling_price"
    if "cost_price" in message:
        return "cost_price"
    if "product_code" in message:
        return "product_code"
    if "name" in message:
        return "name"
    return "body"


def _rule_error(error):
    message = str(error)
    field_name = _field_for_rule_error(message)
    status = 409 if "already used" in message else 400
    return error_response(errors={field_name: message}, status_code=status)


def _parse_page_parameter(name, default, *, maximum):
    values = request.args.getlist(name)
    if not values:
        return default, None
    value = values[0]
    if len(values) != 1 or re.fullmatch(r"[0-9]+", value) is None:
        return None, f"{name} must be an integer."
    try:
        parsed = int(value)
    except ValueError:
        return None, f"{name} must be an integer."
    if parsed <= 0 or parsed > maximum:
        return None, f"{name} must be between 1 and {maximum}."
    return parsed, None


def _list_query():
    errors = {}
    if any(key not in _LIST_FILTERS for key in request.args):
        errors["query"] = "This filter is not allowed."
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        errors["query"] = "Each filter may be provided only once."

    search = request.args.get("q")
    if search is not None:
        search = search.strip()
        if not search:
            errors["q"] = "Search text cannot be empty."
        elif len(search) > PRODUCT_NAME_MAX_LENGTH:
            errors["q"] = (
                f"Search text must be at most {PRODUCT_NAME_MAX_LENGTH} characters."
            )

    category_id = None
    raw_category_id = request.args.get("category_id")
    if raw_category_id is not None:
        if re.fullmatch(r"[0-9]+", raw_category_id) is None:
            errors["category_id"] = "Must be a positive integer."
        else:
            try:
                category_id = int(raw_category_id)
            except ValueError:
                errors["category_id"] = "Must be a positive integer."
            else:
                if category_id <= 0:
                    errors["category_id"] = "Must be a positive integer."

    is_active = None
    raw_is_active = request.args.get("is_active")
    if raw_is_active is not None:
        normalized = raw_is_active.casefold()
        if normalized not in {"true", "false"}:
            errors["is_active"] = "Must be true or false."
        else:
            is_active = normalized == "true"

    page, page_error = _parse_page_parameter(
        "page", _PAGE_DEFAULT, maximum=_PAGE_MAX
    )
    if page_error:
        errors["page"] = page_error
    per_page, per_page_error = _parse_page_parameter(
        "per_page", _PER_PAGE_DEFAULT, maximum=_PER_PAGE_MAX
    )
    if per_page_error:
        errors["per_page"] = per_page_error
    if errors:
        return None, errors
    return {
        "search": search,
        "category_id": category_id,
        "is_active": is_active,
        "page": page,
        "per_page": per_page,
    }, None


def _status_body():
    data, json_error = read_json_object()
    if json_error:
        return None, error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, _STATUS_FIELDS)
    if "is_active" not in data:
        errors["is_active"] = "This field is required."
    elif type(data["is_active"]) is not bool:
        errors["is_active"] = "Must be a boolean."
    if errors:
        return None, error_response(errors=errors, status_code=400)
    return data["is_active"], None


@products_bp.get("")
@require_authenticated_session
def list_products():
    """Search the authenticated business's shared catalog."""
    actor = g.authenticated_session
    if not _catalog_view_allowed(actor):
        return error_response("Permission denied.", status_code=403)

    filters, errors = _list_query()
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = _repository()
    if repository is None:
        return error_response("Product persistence is not available.", status_code=501)
    try:
        result = repository.list_products(
            actor=actor,
            search=filters["search"],
            category_id=filters["category_id"],
            is_active=filters["is_active"],
            limit=filters["per_page"],
            offset=(filters["page"] - 1) * filters["per_page"],
        )
        if type(result) is not ProductListResult or any(
            product.business_id != actor.principal.business_id
            for product in result.products
        ):
            raise RepositoryError("The repository returned invalid catalog data.")
        pages = (
            (result.total_items + filters["per_page"] - 1) // filters["per_page"]
        )
    except RepositoryError as error:
        return _repository_error(error)

    return json_response(
        {
            "data": [_product_json(product) for product in result.products],
            "pagination": {
                "page": filters["page"],
                "per_page": filters["per_page"],
                "total_items": result.total_items,
                "total_pages": pages,
            },
        }
    )


@products_bp.post("")
@require_authenticated_session
def create_product():
    """Create a product through the authorized Owner's business catalog."""
    actor = g.authenticated_session
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)

    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = _product_body_errors(data, partial=False)
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = _repository()
    if repository is None:
        return error_response("Product persistence is not available.", status_code=501)

    try:
        category = _category_or_error(repository, actor, data["category_id"])
        catalog_codes = _catalog_codes(repository, actor)
        proposal = validate_product_creation(
            actor.principal,
            name=data["name"],
            product_code=data["product_code"],
            category_id=data["category_id"],
            selling_price=data["selling_price"],
            cost_price=data.get("cost_price", 0),
            category=category,
            catalog_codes=catalog_codes,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)

    try:
        record = _record_or_error(
            repository.create_product_atomic(actor=actor, data=proposal.data), actor
        )
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _product_json(record), "message": "Product created."},
        status_code=201,
    )


@products_bp.patch("/<product_id>")
@require_authenticated_session
def update_product(product_id):
    """Update approved product fields without touching variants or stock."""
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(product_id)
    if numeric_id is None:
        return error_response(
            errors={"product_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)

    data, json_error = read_json_object()
    if json_error:
        return error_response(json_error, status_code=400)
    errors = _product_body_errors(data, partial=True)
    if errors:
        return error_response(errors=errors, status_code=400)

    repository = _repository()
    if repository is None:
        return error_response("Product persistence is not available.", status_code=501)
    try:
        current = _record_or_error(
            repository.get_product(actor=actor, product_id=numeric_id),
            actor,
            product_id=numeric_id,
        )
        category = _category_or_error(
            repository, actor, data.get("category_id", current.data.category_id)
        )
        catalog_codes = _catalog_codes(repository, actor)
        proposal = validate_product_modification(
            actor.principal,
            product_id=numeric_id,
            product_scope=ResourceScope(current.business_id, None),
            current_data=current.data,
            changes=data,
            category=category,
            catalog_codes=catalog_codes,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except RepositoryNotFoundError as error:
        return _repository_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    except ValueError as error:
        return _rule_error(error)

    try:
        updated = _record_or_error(
            repository.update_product_atomic(
                actor=actor, product_id=numeric_id, data=proposal.data
            ),
            actor,
            product_id=numeric_id,
        )
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _product_json(updated), "message": "Product updated."}
    )


@products_bp.patch("/<product_id>/status")
@require_authenticated_session
def set_product_status(product_id):
    """Activate or deactivate a product without deleting history."""
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(product_id)
    if numeric_id is None:
        return error_response(
            errors={"product_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    is_active, body_error = _status_body()
    if body_error:
        return body_error

    repository = _repository()
    if repository is None:
        return error_response("Product persistence is not available.", status_code=501)
    try:
        current = _record_or_error(
            repository.get_product(actor=actor, product_id=numeric_id),
            actor,
            product_id=numeric_id,
        )
        scope = ResourceScope(current.business_id, None)
        proposal: ProductStatusProposal = (
            validate_product_activation(
                actor.principal, product_id=numeric_id, product_scope=scope
            )
            if is_active
            else validate_product_deactivation(
                actor.principal, product_id=numeric_id, product_scope=scope
            )
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except RepositoryError as error:
        return _repository_error(error)
    except ValueError as error:
        return _rule_error(error)

    try:
        updated = _record_or_error(
            repository.set_product_active_atomic(
                actor=actor, product_id=numeric_id, is_active=proposal.is_active
            ),
            actor,
            product_id=numeric_id,
        )
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _product_json(updated), "message": "Product status updated."}
    )


__all__ = ["products_bp"]
