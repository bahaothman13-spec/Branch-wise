"""SQLAlchemy mappings of the stock-transfer tables in the schema draft.

Importing these mappings registers metadata only; it performs no reflection,
connection, DDL, or application installation.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql

from db import db
from branchwise.suppliers.supplier_model import _identifier_type, _timestamp_type


_TRANSFER_STATUSES = (
    "PENDING",
    "IN_TRANSIT",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
)


def _quantity_type():
    return Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")


stock_transfers = db.Table(
    "stock_transfers",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), nullable=False),
    Column("transfer_reference", String(40), nullable=False),
    Column("request_key", String(120), nullable=False),
    Column("source_branch_id", _identifier_type(), nullable=False),
    Column("destination_branch_id", _identifier_type(), nullable=False),
    Column("requested_by_user_id", _identifier_type(), nullable=False),
    Column(
        "status",
        Enum(
            *_TRANSFER_STATUSES,
            name="stock_transfer_status",
            native_enum=True,
            create_constraint=False,
        ),
        nullable=False,
        server_default=text("'PENDING'"),
    ),
    Column("approved_by_user_id", _identifier_type(), nullable=True),
    Column("dispatched_by_user_id", _identifier_type(), nullable=True),
    Column("received_by_user_id", _identifier_type(), nullable=True),
    Column(
        "requested_at",
        _timestamp_type(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    ),
    Column("dispatched_at", _timestamp_type(), nullable=True),
    Column("received_at", _timestamp_type(), nullable=True),
    Column("closed_at", _timestamp_type(), nullable=True),
    Column("notes", String(500), nullable=True),
    Column("event_history_json", JSON(none_as_null=True), nullable=True),
    UniqueConstraint("business_id", "id", name="uq_stock_transfers_bid_id"),
    UniqueConstraint(
        "business_id", "transfer_reference", name="uq_stock_transfers_reference"
    ),
    UniqueConstraint("business_id", "request_key", name="uq_stock_transfers_request"),
    Index(
        "ix_stock_transfers_1",
        "business_id",
        "source_branch_id",
        "status",
        "requested_at",
    ),
    Index(
        "ix_stock_transfers_2",
        "business_id",
        "destination_branch_id",
        "status",
        "requested_at",
    ),
    CheckConstraint(
        "source_branch_id <> destination_branch_id",
        name="ck_stock_transfers_branches",
    ),
    CheckConstraint(
        "event_history_json IS NULL OR JSON_TYPE(event_history_json)='ARRAY'",
        name="ck_stock_transfers_history",
    ),
    ForeignKeyConstraint(
        ["business_id"],
        ["businesses.id"],
        name="fk_stock_transfers_business_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "source_branch_id"],
        ["branches.business_id", "branches.id"],
        name="fk_stock_transfers_source_branch_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "destination_branch_id"],
        ["branches.business_id", "branches.id"],
        name="fk_stock_transfers_destination_branch_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "requested_by_user_id"],
        ["users.business_id", "users.id"],
        name="fk_stock_transfers_requested_by_user_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "approved_by_user_id"],
        ["users.business_id", "users.id"],
        name="fk_stock_transfers_approved_by_user_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "dispatched_by_user_id"],
        ["users.business_id", "users.id"],
        name="fk_stock_transfers_dispatched_by_user_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "received_by_user_id"],
        ["users.business_id", "users.id"],
        name="fk_stock_transfers_received_by_user_id",
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_0900_ai_ci",
)


stock_transfer_items = db.Table(
    "stock_transfer_items",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), nullable=False),
    Column("transfer_id", _identifier_type(), nullable=False),
    Column("variant_id", _identifier_type(), nullable=False),
    Column("requested_qty", _quantity_type(), nullable=False),
    Column("dispatched_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column("received_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column(
        "returned_to_source_qty",
        _quantity_type(),
        nullable=False,
        server_default=text("0"),
    ),
    Column("written_off_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column("in_transit_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column("missing_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column("resolved_qty", _quantity_type(), nullable=False, server_default=text("0")),
    Column("discrepancy_reported_by", _identifier_type(), nullable=True),
    Column("discrepancy_reported_at", _timestamp_type(), nullable=True),
    Column("discrepancy_resolved_at", _timestamp_type(), nullable=True),
    Column("discrepancy_notes", Text(), nullable=True),
    UniqueConstraint(
        "business_id", "id", name="uq_stock_transfer_items_bid_id"
    ),
    UniqueConstraint(
        "transfer_id", "variant_id", name="uq_stock_transfer_items_variant"
    ),
    CheckConstraint(
        "requested_qty > 0 AND dispatched_qty <= requested_qty",
        name="ck_stock_transfer_items_qty",
    ),
    CheckConstraint(
        "dispatched_qty = received_qty + returned_to_source_qty "
        "+ written_off_qty + in_transit_qty",
        name="ck_stock_transfer_items_conservation",
    ),
    CheckConstraint(
        "resolved_qty <= missing_qty AND missing_qty <= dispatched_qty",
        name="ck_stock_transfer_items_discrepancy",
    ),
    CheckConstraint(
        "missing_qty = 0 OR (discrepancy_reported_at IS NOT NULL "
        "AND discrepancy_reported_by IS NOT NULL)",
        name="ck_stock_transfer_items_discrepancy_report",
    ),
    CheckConstraint(
        "discrepancy_resolved_at IS NULL OR "
        "(missing_qty > 0 AND resolved_qty = missing_qty)",
        name="ck_stock_transfer_items_discrepancy_resolution",
    ),
    ForeignKeyConstraint(
        ["business_id"],
        ["businesses.id"],
        name="fk_stock_transfer_items_business_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "transfer_id"],
        ["stock_transfers.business_id", "stock_transfers.id"],
        name="fk_stock_transfer_items_transfer_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "variant_id"],
        ["product_variants.business_id", "product_variants.id"],
        name="fk_stock_transfer_items_variant_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "discrepancy_reported_by"],
        ["users.business_id", "users.id"],
        name="fk_stock_transfer_items_discrepancy_reported_by",
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_0900_ai_ci",
)
