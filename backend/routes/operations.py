"""Authenticated inventory, sales, return, and approval APIs.

This module composes the existing pure business rules with an explicitly
installed transactional repository. No in-memory or default persistence is
provided; valid writes return 501 until a durable adapter is installed.
"""

from dataclasses import asdict, is_dataclass
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum

from flask import Blueprint, current_app, g, request

from inventory_movement_rules import build_manual_adjustment_movement
from permissions import (
    INVENTORY_ADJUST,
    PRODUCT_CATALOG_APPROVAL,
    PRODUCT_REQUEST_COMMENT,
    PRODUCT_REQUEST_REVIEW,
    PRODUCT_REQUEST_SUBMIT,
    PRODUCTS_VIEW,
    RETURNS_INSPECT,
    RETURNS_RECORD,
    SALE_CANCELLATION_APPROVAL,
    SALE_CANCELLATION_REQUEST,
    SALES_RECORD,
    SALES_VIEW,
    SALES_VIEW_AUTHORIZED,
    ResourceScope,
    Role,
    has_permission,
)
from product_overview_rules import build_product_overview
from product_request_rules import (
    ProductChangeType,
    ProductRequest,
    ProductRequestStatus,
    validate_request_approval,
    validate_request_comment,
    validate_request_rejection,
    validate_request_review,
    validate_request_submission,
)
from repository_contracts import (
    OPERATIONS_REPOSITORY_EXTENSION,
    RepositoryAuthorizationError,
    RepositoryConflictError,
    RepositoryError,
    RepositoryNotFoundError,
)
from request_validation import is_positive_integer, parse_positive_integer_id, read_json_object, validate_allowed_fields
from response_helpers import error_response, json_response
from return_inspection_rules import calculate_inspection_inventory_changes
from return_rules import (
    ReturnRequest,
    ReturnStatus,
    validate_return_submission,
    validate_unlinked_return_verification,
)
from sale_cancellation_rules import (
    SaleCancellationRequest,
    SaleCancellationStatus,
    SaleStatus,
    calculate_restoration_quantities,
    validate_request_approval as validate_cancellation_approval,
    validate_request_rejection as validate_cancellation_rejection,
    validate_request_submission as validate_cancellation_submission,
)
from sale_rules import prepare_sale
from sales_history_rules import get_sale_details, list_sales_history
from session_auth import require_authenticated_session
from stock_adjustment_rules import validate_stock_adjustment
from onboarding_rules import is_branch_operation_ready
from transaction_safety_rules import (
    TransactionOperation,
    create_idempotency_request,
)


operations_bp = Blueprint("operations", __name__, url_prefix="/api")


def _repo():
    return current_app.extensions.get(OPERATIONS_REPOSITORY_EXTENSION)


def _failure(error):
    if isinstance(error, RepositoryAuthorizationError):
        return error_response("Permission denied.", status_code=403)
    if isinstance(error, RepositoryNotFoundError):
        return error_response("Resource not found.", status_code=404)
    if isinstance(error, RepositoryConflictError):
        return error_response("The request conflicts with the current resource state.", status_code=409)
    return error_response("The operation could not be completed.", status_code=500)


def _missing_repo():
    return error_response("Operational persistence is not available.", status_code=501)


def _encode(value):
    if isinstance(value, Decimal):
        return format(value, ".3f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _encode(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


def _result_json(result, message=None, *, status=200):
    data = _encode(result)
    payload = data if isinstance(data, dict) else {"data": data}
    if message:
        payload["message"] = message
    return json_response(payload, status_code=status)


def _business_rule_error(error):
    if isinstance(error, PermissionError):
        return error_response("Permission denied.", status_code=403)
    return error_response(errors={"request": str(error)}, status_code=400)


def _idempotency(actor, operation, payload):
    return create_idempotency_request(
        actor.principal,
        operation,
        request.headers.get("Idempotency-Key"),
        payload,
    )


def _principal_branch_scope(actor):
    principal = actor.principal
    return ResourceScope(
        principal.business_id,
        None if principal.role is Role.OWNER else principal.branch_id,
    )


def _overview_json(view):
    return {
        "product_id": view.product_id,
        "name": view.name,
        "product_code": view.product_code,
        "category_id": view.category_id,
        "category_name": view.category_name,
        "selling_price": _encode(view.selling_price),
        "cost_price": _encode(view.cost_price),
        "total_available_qty": view.total_available_qty,
        "variants": [_encode(row) for row in view.variants],
        "branch_stock": [_encode(row) for row in view.branch_stock],
        "recent_movements": [_encode(row) for row in view.recent_movements],
    }


@operations_bp.get("/products/<product_id>/overview")
@require_authenticated_session
def product_overview(product_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(product_id)
    if numeric_id is None:
        return error_response(errors={"product_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, PRODUCTS_VIEW, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    branch_id = request.args.get("branch_id")
    parsed_branch_id = None if branch_id is None else parse_positive_integer_id(branch_id)
    if branch_id is not None and parsed_branch_id is None:
        return error_response(errors={"branch_id": "Must be a positive integer."}, status_code=400)
    limit = request.args.get("movement_limit", "10")
    parsed_limit = parse_positive_integer_id(limit)
    if parsed_limit is None or parsed_limit > 100:
        return error_response(errors={"movement_limit": "Must be between 1 and 100."}, status_code=400)
    if set(request.args) - {"branch_id", "movement_since", "movement_limit"}:
        return error_response(errors={"query": "This filter is not allowed."}, status_code=400)
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        return error_response(errors={"query": "Each filter may be provided only once."}, status_code=400)
    movement_since = request.args.get("movement_since")
    if movement_since:
        try:
            movement_since = datetime.fromisoformat(movement_since.replace("Z", "+00:00"))
        except ValueError:
            return error_response(errors={"movement_since": "Must be an ISO-8601 timestamp."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_product_overview_inputs(
            actor=actor, product_id=numeric_id,
            branch_id=parsed_branch_id,
        )
        if type(inputs.get("business_branch_count")) is not int or inputs["business_branch_count"] < 1:
            return error_response("Create the first branch before using inventory operations.", status_code=409)
        view = build_product_overview(
            actor.principal,
            product_id=numeric_id,
            product=inputs["product"],
            product_data=inputs["product_data"],
            category=inputs["category"],
            variants=inputs["variants"],
            branches=inputs.get("branches", ()),
            inventory_records=inputs.get("inventory_records"),
            movement_records=inputs.get("movement_records", ()),
            movement_since=movement_since,
            movement_limit=parsed_limit,
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": _overview_json(view)})


@operations_bp.post("/branches/<branch_id>/inventory/adjustments")
@require_authenticated_session
def adjust_inventory(branch_id):
    actor = g.authenticated_session
    numeric_branch = parse_positive_integer_id(branch_id)
    if numeric_branch is None:
        return error_response(errors={"branch_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, INVENTORY_ADJUST, ResourceScope(actor.principal.business_id, numeric_branch)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("product_variant_id", "direction", "quantity", "reason"))
    for key in ("product_variant_id", "direction", "quantity", "reason"):
        if key not in data:
            errors[key] = "This field is required."
    if "product_variant_id" in data and not is_positive_integer(data["product_variant_id"]):
        errors["product_variant_id"] = "Must be a positive integer."
    if "quantity" in data and not is_positive_integer(data["quantity"]):
        errors["quantity"] = "Must be a positive integer."
    if "direction" in data and data["direction"] not in {"INCREASE", "DECREASE"}:
        errors["direction"] = "Must be INCREASE or DECREASE."
    if "reason" in data and (not isinstance(data["reason"], str) or not data["reason"].strip()):
        errors["reason"] = "A meaningful reason is required."
    if errors:
        return error_response(errors=errors, status_code=400)
    try:
        idem = _idempotency(actor, TransactionOperation.MANUAL_STOCK_ADJUSTMENT, data)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_inventory_adjustment_inputs(
            actor=actor, branch_id=numeric_branch, variant_id=data["product_variant_id"]
        )
        if not is_branch_operation_ready(
            actor.principal, INVENTORY_ADJUST, inputs["branch_scope"],
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        result = validate_stock_adjustment(
            actor.principal, actor_id=actor.user_id,
            branch_scope=inputs["branch_scope"], product_scope=inputs["product_scope"],
            variant_scope=inputs["variant_scope"], product_variant_id=data["product_variant_id"],
            inventory_record=inputs.get("inventory_record"), direction=data["direction"],
            quantity=data["quantity"], reason=data["reason"],
            adjustment_reference=idem.request_key,
            existing_adjustment_references=inputs.get("existing_adjustment_references", ()),
        )
        movements = build_manual_adjustment_movement(
            actor.principal, actor_id=actor.user_id,
            branch_scope=inputs["branch_scope"], product_scope=inputs["product_scope"],
            variant_scope=inputs["variant_scope"], product_variant_id=data["product_variant_id"],
            inventory_record=inputs.get("inventory_record"), direction=data["direction"],
            quantity=data["quantity"], reason=data["reason"],
            adjustment_reference=idem.request_key,
            existing_adjustment_references=inputs.get("existing_adjustment_references", ()),
        )
        saved = repository.create_manual_adjustment_atomic(
            actor=actor, movement=movements[0], idempotency_request=idem
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    response = _result_json(saved, "Inventory adjusted.", status=201)
    response.headers["X-Expected-Available-Quantity"] = str(result.expected_available_qty)
    return response


def _request_from_record(record):
    request_value = record.get("request")
    if type(request_value) is not ProductRequest:
        raise RepositoryError("The repository returned an invalid product request.")
    return request_value


@operations_bp.post("/product-requests")
@require_authenticated_session
def submit_product_request():
    actor = g.authenticated_session
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("change_type", "reason", "payload"))
    for key in ("change_type", "reason", "payload"):
        if key not in data:
            errors[key] = "This field is required."
    if "change_type" in data and data["change_type"] not in {item.value for item in ProductChangeType}:
        errors["change_type"] = "Unknown product request change type."
    if "reason" in data and (not isinstance(data["reason"], str) or not data["reason"].strip()):
        errors["reason"] = "A meaningful reason is required."
    if "payload" in data and not isinstance(data["payload"], dict):
        errors["payload"] = "Must be an object containing the requested snapshot."
    if errors:
        return error_response(errors=errors, status_code=400)
    try:
        proposal = validate_request_submission(actor.principal, data["change_type"], is_shared_catalog=True)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        context = repository.get_product_request_submission_context(actor=actor)
        if not is_branch_operation_ready(
            actor.principal, PRODUCT_REQUEST_SUBMIT,
            context["branch_scope"], context["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        saved = repository.submit_product_request_atomic(
            actor=actor, request=proposal, reason=data["reason"].strip(),
            requested_payload=data["payload"],
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Product request submitted.", status=201)


@operations_bp.get("/product-requests")
@require_authenticated_session
def list_product_requests():
    actor = g.authenticated_session
    if actor.principal.role is Role.EMPLOYEE:
        permission = PRODUCT_REQUEST_SUBMIT
    elif actor.principal.role is Role.BRANCH_MANAGER:
        permission = PRODUCT_REQUEST_REVIEW
    else:
        permission = PRODUCT_CATALOG_APPROVAL
    if not has_permission(actor.principal, permission, ResourceScope(actor.principal.business_id, actor.principal.branch_id)):
        return error_response("Permission denied.", status_code=403)
    status = request.args.get("status")
    if set(request.args) - {"status"} or len(request.args.getlist("status")) > 1:
        return error_response(errors={"query": "Unsupported or repeated filter."}, status_code=400)
    if status is not None and status not in {item.value for item in ProductRequestStatus}:
        return error_response(errors={"status": "Unknown request status."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        rows = repository.list_product_requests(actor=actor, status=status)
        for row in rows:
            product_request = _request_from_record(row)
            if actor.principal.role is Role.BRANCH_MANAGER:
                validate_request_review(actor.principal, product_request)
            elif actor.principal.role is Role.OWNER:
                if not has_permission(
                    actor.principal, PRODUCT_CATALOG_APPROVAL,
                    ResourceScope(product_request.business_id, product_request.branch_id),
                ):
                    raise PermissionError("Permission denied.")
            elif (
                product_request.business_id != actor.principal.business_id
                or product_request.branch_id != actor.principal.branch_id
            ):
                raise PermissionError("Permission denied.")
        return json_response({"data": [_encode(row) for row in rows]})
    except PermissionError as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    except ValueError as error:
        return _business_rule_error(error)


@operations_bp.patch("/product-requests/<request_id>/comment")
@require_authenticated_session
def comment_product_request(request_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(request_id)
    if numeric_id is None:
        return error_response(errors={"request_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, PRODUCT_REQUEST_COMMENT, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("comment",))
    if not isinstance(data.get("comment"), str) or not data["comment"].strip():
        errors["comment"] = "A comment is required."
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        product_request = _request_from_record(repository.get_product_request(actor=actor, request_id=numeric_id))
        validate_request_comment(actor.principal, product_request, data["comment"])
        saved = repository.comment_product_request_atomic(actor=actor, request_id=numeric_id, comment=data["comment"].strip())
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Comment added.")


@operations_bp.post("/product-requests/<request_id>/approve")
@require_authenticated_session
def approve_product_request(request_id):
    return _decide_product_request(request_id, approved=True)


@operations_bp.post("/product-requests/<request_id>/reject")
@require_authenticated_session
def reject_product_request(request_id):
    return _decide_product_request(request_id, approved=False)


def _decide_product_request(request_id, *, approved):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(request_id)
    if numeric_id is None:
        return error_response(errors={"request_id": "Must be a positive integer."}, status_code=400)
    if actor.principal.role is not Role.OWNER or not has_permission(
        actor.principal, PRODUCT_CATALOG_APPROVAL, _principal_branch_scope(actor)
    ):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    allowed = () if approved else ("reason",)
    errors = validate_allowed_fields(data, allowed)
    reason = data.get("reason")
    if not approved and (not isinstance(reason, str) or not reason.strip()):
        errors["reason"] = "A rejection reason is required."
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        product_request = _request_from_record(repository.get_product_request(actor=actor, request_id=numeric_id))
        if approved:
            validate_request_approval(actor.principal, product_request)
        else:
            validate_request_rejection(actor.principal, product_request, reason)
        saved = repository.decide_product_request_atomic(
            actor=actor, request_id=numeric_id, approved=approved,
            rejection_reason=None if approved else reason.strip(),
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Product request approved." if approved else "Product request rejected.")


@operations_bp.post("/sales")
@require_authenticated_session
def create_sale():
    actor = g.authenticated_session
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("branch_id", "items"))
    if not is_positive_integer(data.get("branch_id")):
        errors["branch_id"] = "Must be a positive integer."
    if not isinstance(data.get("items"), list) or not data["items"]:
        errors["items"] = "A sale must contain at least one item."
    if errors:
        return error_response(errors=errors, status_code=400)
    try:
        idem = _idempotency(actor, TransactionOperation.SALE, data)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    if not has_permission(
        actor.principal, SALES_RECORD,
        ResourceScope(actor.principal.business_id, data["branch_id"]),
    ):
        return error_response("Permission denied.", status_code=403)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_sale_creation_inputs(actor=actor, branch_id=data["branch_id"], items=data["items"])
        if not is_branch_operation_ready(
            actor.principal, SALES_RECORD, inputs["branch_scope"],
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        proposal = prepare_sale(
            actor.principal, actor_id=actor.user_id,
            branch_scope=inputs["branch_scope"], items=data["items"],
            products=inputs["products"], variants=inputs["variants"],
            inventory_records=inputs["inventory_records"],
        )
        saved = repository.create_sale_atomic(actor=actor, proposal=proposal, idempotency_request=idem)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Sale recorded.", status=201)


@operations_bp.get("/sales")
@require_authenticated_session
def sales_history():
    actor = g.authenticated_session
    sales_permission = SALES_VIEW_AUTHORIZED if actor.principal.role is Role.EMPLOYEE else SALES_VIEW
    if not has_permission(actor.principal, sales_permission, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    allowed = {"sale_reference", "date_from", "date_to", "responsible_user_id", "branch_id", "status", "sort_order", "page", "per_page"}
    if set(request.args) - allowed or any(len(request.args.getlist(key)) > 1 for key in request.args):
        return error_response(errors={"query": "Unsupported or repeated filter."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        result = list_sales_history(
            actor.principal, repository.list_sales(actor=actor),
            sale_reference=request.args.get("sale_reference"),
            date_from=request.args.get("date_from"), date_to=request.args.get("date_to"),
            responsible_user_id=request.args.get("responsible_user_id"),
            branch_id=request.args.get("branch_id"), status=request.args.get("status"),
            sort_order=request.args.get("sort_order", "desc"),
            page=request.args.get("page", 1), per_page=request.args.get("per_page", 20),
        )
    except PermissionError as error:
        return _business_rule_error(error)
    except ValueError as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": _encode(result.data), "pagination": _encode(result.pagination)})


@operations_bp.get("/sales/<sale_id>")
@require_authenticated_session
def sale_details(sale_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(sale_id)
    if numeric_id is None:
        return error_response(errors={"sale_id": "Must be a positive integer."}, status_code=400)
    sales_permission = SALES_VIEW_AUTHORIZED if actor.principal.role is Role.EMPLOYEE else SALES_VIEW
    if not has_permission(actor.principal, sales_permission, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        record = get_sale_details(actor.principal, repository.list_sales(actor=actor), sale_id=numeric_id)
    except PermissionError as error:
        return _business_rule_error(error)
    except ValueError as error:
        return error_response("Sale not found or access denied.", status_code=404)
    except RepositoryError as error:
        return _failure(error)
    return json_response({"data": _encode(record)})


@operations_bp.get("/sale-cancellation-requests")
@require_authenticated_session
def list_cancellation_requests():
    actor = g.authenticated_session
    permission = (
        SALE_CANCELLATION_REQUEST
        if actor.principal.role is Role.EMPLOYEE
        else SALE_CANCELLATION_APPROVAL
    )
    if not has_permission(actor.principal, permission, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    status = request.args.get("status")
    if set(request.args) - {"status"} or len(request.args.getlist("status")) > 1:
        return error_response(errors={"query": "Unsupported or repeated filter."}, status_code=400)
    if status is not None and status not in {item.value for item in SaleCancellationStatus}:
        return error_response(errors={"status": "Unknown cancellation status."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        rows = repository.list_cancellation_requests(actor=actor, status=status)
        for row in rows:
            record = row.get("request")
            if (
                type(record) is not SaleCancellationRequest
                or record.business_id != actor.principal.business_id
                or not has_permission(
                    actor.principal,
                    permission,
                    ResourceScope(record.business_id, record.branch_id),
                )
            ):
                raise RepositoryNotFoundError("Cancellation request outside actor scope.")
        return json_response({"data": [_encode(row) for row in rows]})
    except RepositoryError as error:
        return _failure(error)


@operations_bp.get("/returns")
@require_authenticated_session
def list_returns():
    actor = g.authenticated_session
    permission = RETURNS_INSPECT if actor.principal.role in {Role.OWNER, Role.BRANCH_MANAGER} else RETURNS_RECORD
    if not has_permission(actor.principal, permission, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    status = request.args.get("status")
    if set(request.args) - {"status"} or len(request.args.getlist("status")) > 1:
        return error_response(errors={"query": "Unsupported or repeated filter."}, status_code=400)
    if status is not None and status not in {item.value for item in ReturnStatus}:
        return error_response(errors={"status": "Unknown return status."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        rows = repository.list_returns(actor=actor, status=status)
        for row in rows:
            record = row.get("return_request")
            if (
                type(record) is not ReturnRequest
                or record.business_id != actor.principal.business_id
                or not has_permission(
                    actor.principal,
                    permission,
                    ResourceScope(record.business_id, record.branch_id),
                )
            ):
                raise RepositoryNotFoundError("Return outside actor scope.")
        return json_response({"data": [_encode(row) for row in rows]})
    except RepositoryError as error:
        return _failure(error)


@operations_bp.post("/sales/<sale_id>/cancellation-requests")
@require_authenticated_session
def request_sale_cancellation(sale_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(sale_id)
    if numeric_id is None:
        return error_response(errors={"sale_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, SALE_CANCELLATION_REQUEST, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("reason",))
    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        errors["reason"] = "A cancellation reason is required."
    if errors:
        return error_response(errors=errors, status_code=400)
    try:
        idem = _idempotency(actor, TransactionOperation.RETURN_SUBMISSION, data)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_sale_cancellation_inputs(actor=actor, sale_id=numeric_id)
        if not is_branch_operation_ready(
            actor.principal, SALE_CANCELLATION_REQUEST,
            ResourceScope(inputs["sale"].business_id, inputs["sale"].branch_id),
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        proposal = validate_cancellation_submission(
            actor.principal, inputs["sale"], data["reason"],
            inputs.get("existing_requests", ()), existing_returns=inputs.get("existing_returns", ()),
        )
        saved = repository.request_sale_cancellation_atomic(actor=actor, request=proposal)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Cancellation request submitted.", status=201)


@operations_bp.post("/sale-cancellation-requests/<request_id>/approve")
@require_authenticated_session
def approve_cancellation(request_id):
    return _decide_cancellation(request_id, approved=True)


@operations_bp.post("/sale-cancellation-requests/<request_id>/reject")
@require_authenticated_session
def reject_cancellation(request_id):
    return _decide_cancellation(request_id, approved=False)


def _decide_cancellation(request_id, *, approved):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(request_id)
    if numeric_id is None:
        return error_response(errors={"request_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, SALE_CANCELLATION_APPROVAL, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, () if approved else ("reason",))
    if not approved and (not isinstance(data.get("reason"), str) or not data["reason"].strip()):
        errors["reason"] = "A rejection reason is required."
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_cancellation_decision_inputs(actor=actor, request_id=numeric_id)
        pending_request, sale = inputs["request"], inputs["sale"]
        if not is_branch_operation_ready(
            actor.principal, SALE_CANCELLATION_APPROVAL,
            ResourceScope(sale.business_id, sale.branch_id),
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        returns = inputs.get("existing_returns", ())
        restoration = {}
        idem = None
        if approved:
            validate_cancellation_approval(actor.principal, pending_request, sale, existing_returns=returns)
            approved_request = pending_request.__class__(
                sale_id=pending_request.sale_id, business_id=pending_request.business_id,
                branch_id=pending_request.branch_id, reason=pending_request.reason,
                status=SaleCancellationStatus.APPROVED,
            )
            restoration = calculate_restoration_quantities(sale, approved_request, existing_returns=returns)
            idem = _idempotency(actor, TransactionOperation.APPROVED_SALE_CANCELLATION, {"request_id": numeric_id})
        else:
            validate_cancellation_rejection(actor.principal, pending_request, sale, data["reason"])
        saved = repository.decide_sale_cancellation_atomic(
            actor=actor, request=pending_request, sale=sale,
            restoration_quantities=restoration, approved=approved,
            rejection_reason=None if approved else data["reason"].strip(),
            idempotency_request=idem,
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Cancellation approved." if approved else "Cancellation rejected.")


@operations_bp.post("/returns")
@require_authenticated_session
def submit_return():
    actor = g.authenticated_session
    if not has_permission(actor.principal, RETURNS_RECORD, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("original_sale_id", "branch_id", "reason", "items"))
    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        errors["reason"] = "A return reason is required."
    if not isinstance(data.get("items"), list) or not data["items"]:
        errors["items"] = "A return must contain at least one item."
    for key in ("original_sale_id", "branch_id"):
        if key in data and not is_positive_integer(data[key]):
            errors[key] = "Must be a positive integer."
    if ("original_sale_id" in data) == ("branch_id" in data):
        errors["branch_id"] = "Provide original_sale_id for a linked return or branch_id for an unlinked return."
    if errors:
        return error_response(errors=errors, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_return_submission_inputs(
            actor=actor, sale_id=data.get("original_sale_id"), branch_id=data.get("branch_id")
        )
        branch_scope = inputs.get("branch_scope")
        if inputs.get("sale") is not None:
            branch_scope = ResourceScope(inputs["sale"].business_id, inputs["sale"].branch_id)
        if not is_branch_operation_ready(
            actor.principal, RETURNS_RECORD, branch_scope,
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        proposal = validate_return_submission(
            actor.principal, data["items"], data["reason"],
            sale=inputs.get("sale"), branch_id=inputs.get("branch_scope").branch_id if inputs.get("branch_scope") else None,
            existing_returns=inputs.get("existing_returns", ()),
        )
        saved = repository.submit_return_atomic(actor=actor, request=proposal, idempotency_request=idem)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Return submitted for inspection.", status=201)


@operations_bp.post("/returns/<return_id>/verify")
@require_authenticated_session
def verify_return(return_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(return_id)
    if numeric_id is None:
        return error_response(errors={"return_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, RETURNS_INSPECT, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    if data:
        return error_response(errors={"body": "No fields are accepted."}, status_code=400)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_return_inspection_inputs(actor=actor, return_id=numeric_id)
        return_request = inputs["return_request"]
        if not is_branch_operation_ready(
            actor.principal, RETURNS_INSPECT,
            ResourceScope(return_request.business_id, return_request.branch_id),
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        verified = validate_unlinked_return_verification(actor.principal, return_request)
        saved = repository.verify_unlinked_return_atomic(actor=actor, return_id=numeric_id, return_request=verified)
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Unlinked return verified.")


@operations_bp.post("/returns/<return_id>/inspections")
@require_authenticated_session
def inspect_return(return_id):
    actor = g.authenticated_session
    numeric_id = parse_positive_integer_id(return_id)
    if numeric_id is None:
        return error_response(errors={"return_id": "Must be a positive integer."}, status_code=400)
    if not has_permission(actor.principal, RETURNS_INSPECT, _principal_branch_scope(actor)):
        return error_response("Permission denied.", status_code=403)
    data, body_error = read_json_object()
    if body_error:
        return error_response(body_error, status_code=400)
    errors = validate_allowed_fields(data, ("items",))
    if not isinstance(data.get("items"), list) or not data["items"]:
        errors["items"] = "An inspection must contain at least one item."
    for index, item in enumerate(data.get("items", []) if isinstance(data.get("items"), list) else []):
        if not isinstance(item, dict):
            errors[f"items.{index}"] = "Must be an object."
            continue
        for key in ("return_item_id", "quantity", "condition"):
            if key not in item:
                errors[f"items.{index}.{key}"] = "This field is required."
        if not is_positive_integer(item.get("return_item_id")) or not is_positive_integer(item.get("quantity")):
            errors[f"items.{index}"] = "Return item ID and quantity must be positive integers."
        if item.get("condition") not in {"RESALABLE", "DAMAGED", "UNDER_INSPECTION"}:
            errors[f"items.{index}.condition"] = "Unknown inspection condition."
    if errors:
        return error_response(errors=errors, status_code=400)
    try:
        idem = _idempotency(
            actor, TransactionOperation.RETURN_INSPECTION,
            {"return_id": numeric_id, **data},
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    repository = _repo()
    if repository is None:
        return _missing_repo()
    try:
        inputs = repository.get_return_inspection_inputs(actor=actor, return_id=numeric_id)
        return_request = inputs["return_request"]
        if not is_branch_operation_ready(
            actor.principal, RETURNS_INSPECT,
            ResourceScope(return_request.business_id, return_request.branch_id),
            inputs["business_branch_count"],
        ):
            return error_response("Permission denied.", status_code=403)
        inspection_id = inputs["inspection_id"]
        changes = calculate_inspection_inventory_changes(
            actor.principal, return_request, inputs["returned_items"],
            inspection_id, data["items"], inspection_history=inputs.get("inspection_history", ()),
        )
        saved = repository.inspect_return_atomic(
            actor=actor, return_request=return_request,
            returned_items=inputs["returned_items"], inspection=inspection_id,
            changes=changes, idempotency_request=idem,
        )
    except (PermissionError, ValueError) as error:
        return _business_rule_error(error)
    except RepositoryError as error:
        return _failure(error)
    return _result_json(saved, "Return inspection recorded.", status=201)


__all__ = ["operations_bp"]
