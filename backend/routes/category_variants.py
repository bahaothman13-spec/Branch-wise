"""Authenticated APIs for business categories and product variants."""

from decimal import Decimal, InvalidOperation
import re

from flask import Blueprint, current_app, g, request

from category_rules import (
    CATEGORY_NAME_MAX_LENGTH,
    CategoryContext,
    CategoryData,
    CategoryProposal,
    CategoryStatusProposal,
    validate_category_creation,
    validate_category_modification,
    validate_category_status_change,
)
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
    CatalogCodeEntry,
)
from repository_contracts import (
    CATEGORY_VARIANT_REPOSITORY_EXTENSION,
    CategoryListResult,
    ProductRecord,
    ProductVariantListResult,
    ProductVariantRecord,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import (
    parse_positive_integer_id,
    read_json_object,
    required_string_error,
    validate_allowed_fields,
)
from response_helpers import error_response, json_response
from session_auth import require_authenticated_session
from variant_rules import (
    ProductContext,
    ProductVariantContext,
    VariantData,
    VariantStatusProposal,
    validate_variant_activation,
    validate_variant_creation,
    validate_variant_deactivation,
    validate_variant_modification,
)


catalog_bp = Blueprint("category_variants", __name__, url_prefix="/api")

_CATEGORY_FIELDS = ("name",)
_CATEGORY_STATUS_FIELDS = ("is_active",)
_VARIANT_FIELDS = (
    "sku",
    "size",
    "color",
    "selling_price",
    "cost_price",
    "is_default",
)
_VARIANT_STATUS_FIELDS = ("is_active",)
_PAGE_FILTERS = frozenset({"page", "per_page"})
_ACTIVE_FILTERS = frozenset({"is_active", "page", "per_page"})
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
    # The catalog is business-wide. For non-Owners, validate the grant against
    # the authenticated branch while keeping catalog records branch-independent.
    scope = ResourceScope(
        business_id=principal.business_id,
        branch_id=(None if principal.role is Role.OWNER else principal.branch_id),
    )
    return can_access_resource(principal, PRODUCTS_VIEW, scope)


def _catalog_manage_allowed(actor):
    principal = actor.principal
    return is_trusted_principal(principal) and can_access_resource(
        principal,
        PRODUCT_CATALOG_MANAGE,
        ResourceScope(principal.business_id, None),
    )


def _repository():
    return current_app.extensions.get(CATEGORY_VARIANT_REPOSITORY_EXTENSION)


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
        "The catalog operation could not be completed.", status_code=500
    )


def _page_value(name, default, *, maximum):
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


def _list_filters(*, allow_active):
    allowed = _ACTIVE_FILTERS if allow_active else _PAGE_FILTERS
    errors = {}
    if any(key not in allowed for key in request.args):
        errors["query"] = "This filter is not allowed."
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        errors["query"] = "Each filter may be provided only once."

    is_active = None
    if allow_active and "is_active" in request.args:
        value = request.args.get("is_active", "").casefold()
        if value not in {"true", "false"}:
            errors["is_active"] = "Must be true or false."
        else:
            is_active = value == "true"

    page, page_error = _page_value("page", _PAGE_DEFAULT, maximum=_PAGE_MAX)
    if page_error:
        errors["page"] = page_error
    per_page, per_page_error = _page_value(
        "per_page", _PER_PAGE_DEFAULT, maximum=_PER_PAGE_MAX
    )
    if per_page_error:
        errors["per_page"] = per_page_error
    if errors:
        return None, errors
    return {"is_active": is_active, "page": page, "per_page": per_page}, None


def _pagination(total_items, page, per_page):
    return {
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": (total_items + per_page - 1) // per_page,
    }


def _category_body(*, partial):
    data, json_error = read_json_object()
    if json_error:
        return None, error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, _CATEGORY_FIELDS)
    if partial and not data:
        errors["body"] = "Provide a category name to update."
    if "name" not in data:
        if not partial:
            errors["name"] = "This field is required."
    else:
        field_error = required_string_error(data, "name")
        if field_error:
            errors["name"] = field_error
        elif len(data["name"].strip()) > CATEGORY_NAME_MAX_LENGTH:
            errors["name"] = f"Must be at most {CATEGORY_NAME_MAX_LENGTH} characters."
    if errors:
        return None, error_response(errors=errors, status_code=400)
    return data, None


def _status_body(allowed_fields):
    data, json_error = read_json_object()
    if json_error:
        return None, error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, allowed_fields)
    if "is_active" not in data:
        errors["is_active"] = "This field is required."
    elif type(data["is_active"]) is not bool:
        errors["is_active"] = "Must be a boolean."
    if errors:
        return None, error_response(errors=errors, status_code=400)
    return data["is_active"], None


def _money_error(value):
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(
        value, (int, str)
    ):
        return "Must be an exact decimal string, integer, or null."
    if isinstance(value, str):
        text = value.strip()
        if not text or _DECIMAL_TEXT.fullmatch(text) is None:
            return "Must be a valid decimal value."
    else:
        text = str(value)
    try:
        amount = Decimal(text)
        normalized = amount.quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        return "Must fit DECIMAL(14,3)."
    if not amount.is_finite():
        return "Must be a finite decimal value."
    if amount < 0:
        return "Must be non-negative."
    if amount > Decimal("99999999999.999"):
        return "Must fit DECIMAL(14,3)."
    if normalized != amount:
        return "May have at most 3 decimal places."
    return None


def _variant_body(*, partial):
    data, json_error = read_json_object()
    if json_error:
        return None, error_response(json_error, status_code=400)
    errors = validate_allowed_fields(data, _VARIANT_FIELDS)
    if partial and not data:
        errors["body"] = "Provide at least one variant field to update."

    if "sku" not in data:
        if not partial:
            errors["sku"] = "This field is required."
    else:
        field_error = required_string_error(data, "sku")
        if field_error:
            errors["sku"] = field_error
        elif len(data["sku"].strip()) > PRODUCT_CODE_MAX_LENGTH:
            errors["sku"] = f"Must be at most {PRODUCT_CODE_MAX_LENGTH} characters."

    for field in ("size", "color"):
        if field in data and data[field] is not None:
            if not isinstance(data[field], str):
                errors[field] = "Must be a string or null."
            elif not data[field].strip():
                errors[field] = "Must not be empty."
    if "is_default" in data and type(data["is_default"]) is not bool:
        errors["is_default"] = "Must be a boolean."
    for field in ("selling_price", "cost_price"):
        if field in data:
            error = _money_error(data[field])
            if error:
                errors[field] = error
    if errors:
        return None, error_response(errors=errors, status_code=400)
    return data, None


def _rule_error(error):
    message = str(error)
    if "already used" in message:
        field = "name" if "name" in message else "sku"
        status = 409
    elif "code" in message:
        field = "sku"
        status = 400
    elif "selling_price" in message:
        field = "selling_price"
        status = 400
    elif "cost_price" in message:
        field = "cost_price"
        status = 400
    elif "size" in message:
        field = "size"
        status = 400
    elif "color" in message:
        field = "color"
        status = 400
    elif "is_default" in message or "default variant" in message:
        field = "is_default"
        status = 409 if "already has" in message else 400
    elif "name" in message:
        field = "name"
        status = 400
    else:
        field = "body"
        status = 400
    return error_response(errors={field: message}, status_code=status)


def _category_or_error(value, actor, *, category_id=None):
    if type(value) is not CategoryContext:
        raise RepositoryError("The repository returned invalid category data.")
    if (
        type(value.category_id) is not int
        or value.category_id <= 0
        or not is_valid_resource_scope(value.scope)
        or value.scope.branch_id is not None
        or type(value.is_active) is not bool
        or not isinstance(value.name, str)
        or not value.name.strip()
        or len(value.name.strip()) > CATEGORY_NAME_MAX_LENGTH
    ):
        raise RepositoryError("The repository returned invalid category data.")
    if (
        (category_id is not None and value.category_id != category_id)
        or value.scope.business_id != actor.principal.business_id
    ):
        raise RepositoryNotFoundError("Category is outside the actor's business.")
    return value


def _all_categories(repository, actor):
    result = repository.list_all_categories(actor=actor)
    if type(result) not in (tuple, list):
        raise RepositoryError("The repository returned invalid category data.")
    return tuple(_category_or_error(item, actor) for item in result)


def _product_or_error(value, actor, *, product_id=None):
    if type(value) is not ProductRecord:
        raise RepositoryError("The repository returned invalid product data.")
    if (
        value.business_id != actor.principal.business_id
        or (product_id is not None and value.product_id != product_id)
    ):
        raise RepositoryNotFoundError("Product is outside the actor's business.")
    return value


def _variant_record_or_error(value, actor, *, product_id, variant_id=None):
    if type(value) is not ProductVariantRecord:
        raise RepositoryError("The repository returned invalid variant data.")
    if (
        value.business_id != actor.principal.business_id
        or value.data.product_id != product_id
        or (variant_id is not None and value.variant_id != variant_id)
    ):
        raise RepositoryNotFoundError("Variant is outside the actor's business.")
    return value


def _catalog_codes(repository, actor):
    values = repository.list_catalog_codes(actor=actor)
    if type(values) not in (tuple, list) or any(
        type(entry) is not CatalogCodeEntry
        or entry.business_id != actor.principal.business_id
        or not isinstance(entry.code, str)
        or not entry.code.strip()
        or entry.resource_type not in {"product", "variant"}
        or type(entry.resource_id) is not int
        or entry.resource_id <= 0
        for entry in values
    ):
        raise RepositoryError("The repository returned invalid catalog codes.")
    return tuple(values)


def _variant_context(record):
    return ProductVariantContext(
        variant_id=record.variant_id,
        product_id=record.data.product_id,
        scope=ResourceScope(record.business_id, None),
        is_default=record.data.is_default,
    )


def _variant_contexts(repository, actor, product_id):
    values = repository.list_product_variant_contexts(
        actor=actor, product_id=product_id
    )
    if type(values) not in (tuple, list) or any(
        type(value) is not ProductVariantContext for value in values
    ):
        raise RepositoryError("The repository returned invalid variant contexts.")
    for value in values:
        if (
            type(value.variant_id) is not int
            or value.variant_id <= 0
            or type(value.product_id) is not int
            or value.product_id != product_id
            or not is_valid_resource_scope(value.scope)
            or value.scope.business_id != actor.principal.business_id
            or value.scope.branch_id is not None
            or type(value.is_default) is not bool
        ):
            raise RepositoryError("The repository returned out-of-scope variant data.")
    return tuple(values)


def _catalog_code_exists(repository, actor, code, *, exclude_variant_id=None):
    exists = repository.catalog_code_exists(
        actor=actor,
        code=code,
        exclude_resource_type="variant" if exclude_variant_id is not None else None,
        exclude_resource_id=exclude_variant_id,
    )
    if type(exists) is not bool:
        raise RepositoryError("The repository returned an invalid code check.")
    return exists


def _category_json(category):
    return {
        "category_id": category.category_id,
        "name": category.name,
        "is_active": category.is_active,
    }


def _variant_json(record):
    data = record.data
    return {
        "variant_id": record.variant_id,
        "product_id": data.product_id,
        "sku": data.code,
        "size": data.size,
        "color": data.color,
        "selling_price": None
        if data.selling_price is None
        else format(data.selling_price, ".3f"),
        "cost_price": None
        if data.cost_price is None
        else format(data.cost_price, ".3f"),
        "is_default": data.is_default,
        "is_active": record.is_active,
    }


@catalog_bp.get("/categories")
@require_authenticated_session
def list_categories():
    actor = g.authenticated_session
    if not _catalog_view_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    filters, errors = _list_filters(allow_active=True)
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        result = repository.list_categories(
            actor=actor,
            is_active=filters["is_active"],
            limit=filters["per_page"],
            offset=(filters["page"] - 1) * filters["per_page"],
        )
        if type(result) is not CategoryListResult or any(
            category.scope.business_id != actor.principal.business_id
            for category in result.categories
        ):
            raise RepositoryError("The repository returned invalid category data.")
        categories = [
            _category_or_error(category, actor) for category in result.categories
        ]
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {
            "data": [_category_json(category) for category in categories],
            "pagination": _pagination(
                result.total_items, filters["page"], filters["per_page"]
            ),
        }
    )


@catalog_bp.post("/categories")
@require_authenticated_session
def create_category():
    actor = g.authenticated_session
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _category_body(partial=False)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        existing = _all_categories(repository, actor)
        proposal: CategoryProposal = validate_category_creation(
            actor.principal, name=data["name"], existing_categories=existing
        )
        created = _category_or_error(
            repository.create_category_atomic(actor=actor, data=proposal.data), actor
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _category_json(created), "message": "Category created."},
        status_code=201,
    )


@catalog_bp.patch("/categories/<category_id>")
@require_authenticated_session
def update_category(category_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(category_id)
    if numeric_id is None:
        return error_response(
            errors={"category_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _category_body(partial=True)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        current = _category_or_error(
            repository.get_category_context(actor=actor, category_id=numeric_id),
            actor,
            category_id=numeric_id,
        )
        proposal: CategoryProposal = validate_category_modification(
            actor.principal,
            category_id=numeric_id,
            category=current,
            changes=data,
            existing_categories=_all_categories(repository, actor),
        )
        updated = _category_or_error(
            repository.update_category_atomic(
                actor=actor, category_id=numeric_id, data=proposal.data
            ),
            actor,
            category_id=numeric_id,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _category_json(updated), "message": "Category updated."}
    )


@catalog_bp.patch("/categories/<category_id>/status")
@require_authenticated_session
def set_category_status(category_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(category_id)
    if numeric_id is None:
        return error_response(
            errors={"category_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    is_active, body_error = _status_body(_CATEGORY_STATUS_FIELDS)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        current = _category_or_error(
            repository.get_category_context(actor=actor, category_id=numeric_id),
            actor,
            category_id=numeric_id,
        )
        proposal: CategoryStatusProposal = validate_category_status_change(
            actor.principal,
            category_id=numeric_id,
            category=current,
            is_active=is_active,
        )
        updated = _category_or_error(
            repository.set_category_active_atomic(
                actor=actor,
                category_id=numeric_id,
                is_active=proposal.is_active,
            ),
            actor,
            category_id=numeric_id,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _category_json(updated), "message": "Category status updated."}
    )


@catalog_bp.get("/products/<product_id>/variants")
@require_authenticated_session
def list_product_variants(product_id):
    actor = g.authenticated_session
    numeric_product_id = parse_positive_integer_id(product_id)
    if numeric_product_id is None:
        return error_response(
            errors={"product_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_view_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    filters, errors = _list_filters(allow_active=True)
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        _product_or_error(
            repository.get_product(actor=actor, product_id=numeric_product_id),
            actor,
            product_id=numeric_product_id,
        )
        result = repository.list_product_variants(
            actor=actor,
            product_id=numeric_product_id,
            is_active=filters["is_active"],
            limit=filters["per_page"],
            offset=(filters["page"] - 1) * filters["per_page"],
        )
        if type(result) is not ProductVariantListResult:
            raise RepositoryError("The repository returned invalid variant data.")
        variants = [
            _variant_record_or_error(
                variant, actor, product_id=numeric_product_id
            )
            for variant in result.variants
        ]
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {
            "data": [_variant_json(variant) for variant in variants],
            "pagination": _pagination(
                result.total_items, filters["page"], filters["per_page"]
            ),
        }
    )


@catalog_bp.post("/products/<product_id>/variants")
@require_authenticated_session
def create_product_variant(product_id):
    actor = g.authenticated_session
    numeric_product_id = parse_positive_integer_id(product_id)
    if numeric_product_id is None:
        return error_response(
            errors={"product_id": "Must be a positive integer."}, status_code=400
        )
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _variant_body(partial=False)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        product_record = _product_or_error(
            repository.get_product(actor=actor, product_id=numeric_product_id),
            actor,
            product_id=numeric_product_id,
        )
        product = ProductContext(
            product_id=product_record.product_id,
            scope=ResourceScope(product_record.business_id, None),
        )
        if _catalog_code_exists(repository, actor, data["sku"]):
            return error_response(
                errors={"sku": "SKU is already used in this business catalog."},
                status_code=409,
            )
        proposal = validate_variant_creation(
            actor.principal,
            product_id=numeric_product_id,
            product=product,
            code=data["sku"],
            size=data.get("size"),
            color=data.get("color"),
            selling_price=data.get("selling_price"),
            cost_price=data.get("cost_price"),
            is_default=data.get("is_default", False),
            catalog_codes=_catalog_codes(repository, actor),
            existing_variants=_variant_contexts(
                repository, actor, numeric_product_id
            ),
        )
        created = _variant_record_or_error(
            repository.create_variant_atomic(actor=actor, data=proposal.data),
            actor,
            product_id=numeric_product_id,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _variant_json(created), "message": "Variant created."},
        status_code=201,
    )


@catalog_bp.patch("/products/<product_id>/variants/<variant_id>")
@require_authenticated_session
def update_product_variant(product_id, variant_id):
    actor = g.authenticated_session
    numeric_product_id = parse_positive_integer_id(product_id)
    numeric_variant_id = parse_positive_integer_id(variant_id)
    errors = {}
    if numeric_product_id is None:
        errors["product_id"] = "Must be a positive integer."
    if numeric_variant_id is None:
        errors["variant_id"] = "Must be a positive integer."
    if errors:
        return error_response(errors=errors, status_code=400)
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    data, body_error = _variant_body(partial=True)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        product_record = _product_or_error(
            repository.get_product(actor=actor, product_id=numeric_product_id),
            actor,
            product_id=numeric_product_id,
        )
        current_record = _variant_record_or_error(
            repository.get_product_variant(
                actor=actor,
                product_id=numeric_product_id,
                variant_id=numeric_variant_id,
            ),
            actor,
            product_id=numeric_product_id,
            variant_id=numeric_variant_id,
        )
        product = ProductContext(
            product_id=product_record.product_id,
            scope=ResourceScope(product_record.business_id, None),
        )
        variant = _variant_context(current_record)
        current = current_record.data
        changes = {
            ("code" if key == "sku" else key): value
            for key, value in data.items()
        }
        proposed_code = changes.get("code", current.code)
        if _catalog_code_exists(
            repository,
            actor,
            proposed_code,
            exclude_variant_id=numeric_variant_id,
        ):
            return error_response(
                errors={"sku": "SKU is already used in this business catalog."},
                status_code=409,
            )
        proposal = validate_variant_modification(
            actor.principal,
            product_id=numeric_product_id,
            product=product,
            product_variant_id=numeric_variant_id,
            variant=variant,
            current_data=current,
            changes=changes,
            catalog_codes=_catalog_codes(repository, actor),
            existing_variants=_variant_contexts(
                repository, actor, numeric_product_id
            ),
        )
        updated = _variant_record_or_error(
            repository.update_variant_atomic(
                actor=actor,
                product_id=numeric_product_id,
                variant_id=numeric_variant_id,
                data=proposal.data,
            ),
            actor,
            product_id=numeric_product_id,
            variant_id=numeric_variant_id,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _variant_json(updated), "message": "Variant updated."}
    )


@catalog_bp.patch("/products/<product_id>/variants/<variant_id>/status")
@require_authenticated_session
def set_product_variant_status(product_id, variant_id):
    actor = g.authenticated_session
    numeric_product_id = parse_positive_integer_id(product_id)
    numeric_variant_id = parse_positive_integer_id(variant_id)
    errors = {}
    if numeric_product_id is None:
        errors["product_id"] = "Must be a positive integer."
    if numeric_variant_id is None:
        errors["variant_id"] = "Must be a positive integer."
    if errors:
        return error_response(errors=errors, status_code=400)
    if not _catalog_manage_allowed(actor):
        return error_response("Permission denied.", status_code=403)
    is_active, body_error = _status_body(_VARIANT_STATUS_FIELDS)
    if body_error:
        return body_error
    repository = _repository()
    if repository is None:
        return error_response("Category and variant persistence is not available.", status_code=501)
    try:
        product_record = _product_or_error(
            repository.get_product(actor=actor, product_id=numeric_product_id),
            actor,
            product_id=numeric_product_id,
        )
        current = _variant_record_or_error(
            repository.get_product_variant(
                actor=actor,
                product_id=numeric_product_id,
                variant_id=numeric_variant_id,
            ),
            actor,
            product_id=numeric_product_id,
            variant_id=numeric_variant_id,
        )
        product = ProductContext(
            product_id=product_record.product_id,
            scope=ResourceScope(product_record.business_id, None),
        )
        variant = _variant_context(current)
        proposal: VariantStatusProposal = (
            validate_variant_activation(
                actor.principal,
                product_id=numeric_product_id,
                product=product,
                product_variant_id=numeric_variant_id,
                variant=variant,
            )
            if is_active
            else validate_variant_deactivation(
                actor.principal,
                product_id=numeric_product_id,
                product=product,
                product_variant_id=numeric_variant_id,
                variant=variant,
            )
        )
        updated = _variant_record_or_error(
            repository.set_variant_active_atomic(
                actor=actor,
                product_id=numeric_product_id,
                variant_id=numeric_variant_id,
                is_active=proposal.is_active,
            ),
            actor,
            product_id=numeric_product_id,
            variant_id=numeric_variant_id,
        )
    except PermissionError:
        return error_response("Permission denied.", status_code=403)
    except ValueError as error:
        return _rule_error(error)
    except RepositoryError as error:
        return _repository_error(error)
    return json_response(
        {"data": _variant_json(updated), "message": "Variant status updated."}
    )


__all__ = ["catalog_bp"]
