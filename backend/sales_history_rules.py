"""Read-only rules for listing sales history and viewing sale details.

Callers must build principals and sale snapshots from authenticated identity
and trusted server-side records. This module does not load or change data.
Historical amounts are validated only against the price snapshots stored on
the sale; current catalog prices are never consulted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Generic, TypeVar

from isolation_rules import ACCESS_DENIED_MESSAGE, filter_accessible_resources
from permissions import (
    ROLE_PERMISSIONS,
    SALES_VIEW,
    SALES_VIEW_AUTHORIZED,
    AuthenticatedPrincipal,
    ResourceScope,
    Role,
    is_trusted_principal,
    is_valid_resource_scope,
)
from request_validation import is_positive_integer
from sale_cancellation_rules import SaleStatus


_MAX_MONEY = Decimal("999999999.999")
_MILLIUNIT = Decimal("0.001")
_MAX_PER_PAGE = 100
_PAGE_DIGITS = re.compile(r"^[0-9]+$")
_SALE_STATUSES = (SaleStatus.COMPLETED, SaleStatus.CANCELLED)
T = TypeVar("T")


@dataclass(frozen=True)
class SaleHistoryLine:
    """Product and variant labels and prices captured on a sale line."""

    product_id: int
    product_name: str
    product_variant_id: int
    variant_name: str
    quantity: int
    unit_price_snapshot: Decimal
    line_subtotal: Decimal


@dataclass(frozen=True)
class SaleHistoryRecord:
    """Trusted, complete sale snapshot supplied by a future read layer."""

    sale_id: int
    sale_reference: str
    sale_date: datetime
    business_id: int
    branch_id: int
    branch_scope: ResourceScope
    branch_name: str
    responsible_user_id: int
    responsible_user_name: str
    status: SaleStatus
    items: tuple[SaleHistoryLine, ...] | list[SaleHistoryLine]
    total_amount: Decimal


@dataclass(frozen=True)
class SaleHistorySummary:
    """Fields intended for one row in a paginated sales history list."""

    sale_id: int
    sale_reference: str
    sale_date: datetime
    branch_id: int
    branch_name: str
    responsible_user_id: int
    responsible_user_name: str
    status: SaleStatus
    total_amount: Decimal


@dataclass(frozen=True)
class PaginationParameters:
    page: int
    per_page: int


@dataclass(frozen=True)
class PaginationMetadata:
    page: int
    per_page: int
    total_items: int
    total_pages: int


@dataclass(frozen=True)
class PaginatedResult(Generic[T]):
    data: tuple[T, ...]
    pagination: PaginationMetadata


def _parse_positive_integer(value, field_name):
    if is_positive_integer(value):
        return value
    if isinstance(value, str) and _PAGE_DIGITS.fullmatch(value):
        try:
            parsed = int(value)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    raise ValueError(f"{field_name} must be a positive integer.")


def validate_pagination(page=1, per_page=20) -> PaginationParameters:
    """Validate API-contract pagination values without depending on Flask."""
    parsed_page = _parse_positive_integer(page, "page")
    parsed_per_page = _parse_positive_integer(per_page, "per_page")
    if parsed_per_page > _MAX_PER_PAGE:
        raise ValueError(f"per_page must not exceed {_MAX_PER_PAGE}.")
    return PaginationParameters(page=parsed_page, per_page=parsed_per_page)


def paginate_read_only(items, *, page=1, per_page=20) -> PaginatedResult:
    """Return a page and API-contract metadata without changing the input."""
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, (list, tuple)):
        raise ValueError("items must be a list or tuple.")
    parameters = validate_pagination(page, per_page)
    total_items = len(items)
    total_pages = (total_items + parameters.per_page - 1) // parameters.per_page
    start = (parameters.page - 1) * parameters.per_page
    end = start + parameters.per_page
    return PaginatedResult(
        data=tuple(items[start:end]),
        pagination=PaginationMetadata(
            page=parameters.page,
            per_page=parameters.per_page,
            total_items=total_items,
            total_pages=total_pages,
        ),
    )


def _trusted_sale_status(status):
    if isinstance(status, SaleStatus):
        return status
    try:
        return SaleStatus(status)
    except (TypeError, ValueError):
        raise ValueError("The trusted sale has an invalid status.") from None


def _filter_sale_status(status):
    if isinstance(status, SaleStatus):
        return status
    if not isinstance(status, str):
        raise ValueError("status must be Completed or Cancelled.")
    normalized = status.strip().casefold()
    for sale_status in _SALE_STATUSES:
        if normalized in (sale_status.name.casefold(), sale_status.value.casefold()):
            return sale_status
    raise ValueError("status must be Completed or Cancelled.")


def _validate_name(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")


def _validate_money(value, field_name):
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal amount.")
    if value < 0 or value > _MAX_MONEY:
        raise ValueError(f"{field_name} must fit DECIMAL(12,3).")
    try:
        normalized = value.quantize(_MILLIUNIT)
    except InvalidOperation:
        raise ValueError(f"{field_name} must fit DECIMAL(12,3).") from None
    if value != normalized:
        raise ValueError(f"{field_name} must fit DECIMAL(12,3).")
    return int(value * 1000)


def _validate_line(line):
    if type(line) is not SaleHistoryLine:
        raise ValueError("Each trusted sale line must be a SaleHistoryLine.")
    if not is_positive_integer(line.product_id):
        raise ValueError("product_id must be a positive integer.")
    _validate_name(line.product_name, "product_name")
    if not is_positive_integer(line.product_variant_id):
        raise ValueError("product_variant_id must be a positive integer.")
    _validate_name(line.variant_name, "variant_name")
    if not is_positive_integer(line.quantity):
        raise ValueError("quantity must be a positive integer.")

    price_milliunits = _validate_money(
        line.unit_price_snapshot, "unit_price_snapshot"
    )
    subtotal_milliunits = _validate_money(line.line_subtotal, "line_subtotal")
    expected_subtotal = price_milliunits * line.quantity
    if expected_subtotal > 999_999_999_999:
        raise ValueError("Sale line subtotal must fit DECIMAL(12,3).")
    if subtotal_milliunits != expected_subtotal:
        raise ValueError("The sale line subtotal does not match its price snapshot.")
    return subtotal_milliunits


def _validate_sale_record(sale):
    if type(sale) is not SaleHistoryRecord:
        raise ValueError("Each trusted sale must be a SaleHistoryRecord.")
    if not is_positive_integer(sale.sale_id):
        raise ValueError("sale_id must be a positive integer.")
    _validate_name(sale.sale_reference, "sale_reference")
    if sale.sale_reference != sale.sale_reference.strip():
        raise ValueError("sale_reference must not have surrounding whitespace.")
    if not isinstance(sale.sale_date, datetime) or sale.sale_date.tzinfo is None:
        raise ValueError("sale_date must be a timezone-aware timestamp.")
    try:
        if sale.sale_date.utcoffset() is None:
            raise ValueError("sale_date must be timezone-aware.")
        utc_sale_date = _utc(sale.sale_date)
    except (OverflowError, TypeError, ValueError):
        utc_sale_date = None
    if utc_sale_date is None:
        raise ValueError("sale_date must be a timezone-aware timestamp.")
    if not is_positive_integer(sale.business_id):
        raise ValueError("business_id must be a positive integer.")
    if not is_positive_integer(sale.branch_id):
        raise ValueError("branch_id must be a positive integer.")
    if (
        not is_valid_resource_scope(sale.branch_scope)
        or sale.branch_scope.business_id != sale.business_id
        or sale.branch_scope.branch_id != sale.branch_id
    ):
        raise ValueError("The trusted sale branch context is inconsistent.")
    _validate_name(sale.branch_name, "branch_name")
    if not is_positive_integer(sale.responsible_user_id):
        raise ValueError("responsible_user_id must be a positive integer.")
    _validate_name(sale.responsible_user_name, "responsible_user_name")
    _trusted_sale_status(sale.status)
    if not isinstance(sale.items, (list, tuple)) or not sale.items:
        raise ValueError("A trusted sale must contain at least one item.")

    total_milliunits = sum(_validate_line(line) for line in sale.items)
    if total_milliunits > 999_999_999_999:
        raise ValueError("Sale total must fit DECIMAL(12,3).")
    stored_total = _validate_money(sale.total_amount, "total_amount")
    if total_milliunits != stored_total:
        raise ValueError("The sale total does not match its historical line amounts.")
    return SaleHistoryRecord(
        sale_id=sale.sale_id,
        sale_reference=sale.sale_reference,
        sale_date=utc_sale_date,
        business_id=sale.business_id,
        branch_id=sale.branch_id,
        branch_scope=sale.branch_scope,
        branch_name=sale.branch_name,
        responsible_user_id=sale.responsible_user_id,
        responsible_user_name=sale.responsible_user_name,
        status=_trusted_sale_status(sale.status),
        items=tuple(sale.items),
        total_amount=sale.total_amount,
    )


def _validated_sales(sales):
    if isinstance(sales, (str, bytes, Mapping)) or not isinstance(sales, (list, tuple)):
        raise ValueError("sales must be a list or tuple of trusted sale records.")
    validated = tuple(_validate_sale_record(sale) for sale in sales)
    sale_ids = [sale.sale_id for sale in validated]
    if len(sale_ids) != len(set(sale_ids)):
        raise ValueError("Trusted sales contain a duplicate sale_id.")
    return validated


def _required_view_permission(principal):
    if not is_trusted_principal(principal):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if principal.role is Role.EMPLOYEE:
        permission = SALES_VIEW_AUTHORIZED
    else:
        permission = SALES_VIEW
    if (
        permission not in ROLE_PERMISSIONS[principal.role]
        or permission not in principal.permissions
    ):
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    return permission


def _authorized_sales(principal, sales):
    permission = _required_view_permission(principal)
    # Validate every server-loaded row before filtering; malformed ownership or
    # amount snapshots must never be silently treated as an ordinary omission.
    validated = _validated_sales(sales)
    return filter_accessible_resources(
        principal,
        permission,
        validated,
        scope_getter=lambda sale: sale.branch_scope,
    )


def _parse_optional_date(value, field_name):
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a date in YYYY-MM-DD format.")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.isoformat() == value:
            return parsed
    raise ValueError(f"{field_name} must be a date in YYYY-MM-DD format.")


def _optional_filter_id(value, field_name):
    if value is None:
        return None
    return _parse_positive_integer(value, field_name)


def _utc(value):
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("sale_date must be a valid timezone-aware timestamp.") from None


def _sale_summary(sale):
    return SaleHistorySummary(
        sale_id=sale.sale_id,
        sale_reference=sale.sale_reference,
        sale_date=_utc(sale.sale_date),
        branch_id=sale.branch_id,
        branch_name=sale.branch_name,
        responsible_user_id=sale.responsible_user_id,
        responsible_user_name=sale.responsible_user_name,
        status=_trusted_sale_status(sale.status),
        total_amount=sale.total_amount,
    )


def list_sales_history(
    principal,
    sales,
    *,
    sale_reference=None,
    date_from=None,
    date_to=None,
    responsible_user_id=None,
    branch_id=None,
    status=None,
    sort_order="desc",
    page=1,
    per_page=20,
) -> PaginatedResult[SaleHistorySummary]:
    """Filter, authorize, sort, and paginate trusted sales in read-only form."""
    if sale_reference is not None:
        _validate_name(sale_reference, "sale_reference")
        sale_reference = sale_reference.strip()
    date_from = _parse_optional_date(date_from, "date_from")
    date_to = _parse_optional_date(date_to, "date_to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("date_from must not be after date_to.")
    responsible_user_id = _optional_filter_id(
        responsible_user_id, "responsible_user_id"
    )
    branch_id = _optional_filter_id(branch_id, "branch_id")
    requested_status = None if status is None else _filter_sale_status(status)
    if not isinstance(sort_order, str) or sort_order.casefold() not in ("asc", "desc"):
        raise ValueError("sort_order must be 'asc' or 'desc'.")
    parameters = validate_pagination(page, per_page)

    authorized = _authorized_sales(principal, sales)
    matching = []
    for sale in authorized:
        sale_status = _trusted_sale_status(sale.status)
        sale_day = _utc(sale.sale_date).date()
        if sale_reference is not None and sale.sale_reference != sale_reference:
            continue
        if date_from is not None and sale_day < date_from:
            continue
        if date_to is not None and sale_day > date_to:
            continue
        if (
            responsible_user_id is not None
            and sale.responsible_user_id != responsible_user_id
        ):
            continue
        if branch_id is not None and sale.branch_id != branch_id:
            continue
        if requested_status is not None and sale_status is not requested_status:
            continue
        matching.append(sale)

    reverse = sort_order.casefold() == "desc"
    matching.sort(key=lambda sale: (_utc(sale.sale_date), sale.sale_id), reverse=reverse)
    summaries = [_sale_summary(sale) for sale in matching]
    return paginate_read_only(
        summaries,
        page=parameters.page,
        per_page=parameters.per_page,
    )


def get_sale_details(
    principal,
    sales,
    *,
    sale_id=None,
    sale_reference=None,
) -> SaleHistoryRecord:
    """Return full details for one accessible sale, denying foreign lookups."""
    if (sale_id is None) == (sale_reference is None):
        raise ValueError("Provide exactly one of sale_id or sale_reference.")
    if sale_id is not None:
        sale_id = _parse_positive_integer(sale_id, "sale_id")
    else:
        _validate_name(sale_reference, "sale_reference")
        sale_reference = sale_reference.strip()

    authorized = _authorized_sales(principal, sales)
    matches = [
        sale
        for sale in authorized
        if (
            sale.sale_id == sale_id
            if sale_id is not None
            else sale.sale_reference == sale_reference
        )
    ]
    if not matches:
        # The same denial covers an absent record and an inaccessible one.
        raise PermissionError(ACCESS_DENIED_MESSAGE)
    if len(matches) > 1:
        raise ValueError("The sale lookup is ambiguous.")
    return matches[0]


__all__ = [
    "PaginatedResult",
    "PaginationMetadata",
    "PaginationParameters",
    "SaleHistoryLine",
    "SaleHistoryRecord",
    "SaleHistorySummary",
    "get_sale_details",
    "list_sales_history",
    "paginate_read_only",
    "validate_pagination",
]
