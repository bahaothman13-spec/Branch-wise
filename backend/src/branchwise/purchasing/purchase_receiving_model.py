"""Approved existing receipt mappings; importing this module executes no DDL.

The schema's separate business-scoped receipt and PO-item FKs do not enforce
that a receipt line references its header's purchase order. The read adapter
checks that relationship and fails closed; no extra constraint is invented.
"""
from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer,
    String, UniqueConstraint, text,
)
from sqlalchemy.dialects import mysql

from db import db
from branchwise.suppliers.supplier_model import _identifier_type, _timestamp_type
# Register the existing shared mappings, never competing PO table definitions.
from branchwise.purchasing.purchase_order_model import purchase_order_items, purchase_orders  # noqa: F401


purchase_receipts = db.Table(
    "purchase_receipts",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), ForeignKey(
        "businesses.id", name="fk_purchase_receipts_business_id"
    ), nullable=False),
    Column("purchase_order_id", _identifier_type(), nullable=False),
    Column("receipt_reference", String(40), nullable=False),
    Column("request_key", String(120), nullable=False),
    Column("received_by_user_id", _identifier_type(), nullable=False),
    Column("received_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6)")),
    Column("notes", String(500)),
    UniqueConstraint("business_id", "id", name="uq_purchase_receipts_bid_id"),
    UniqueConstraint("business_id", "receipt_reference", name="uq_purchase_receipts_reference"),
    UniqueConstraint("business_id", "request_key", name="uq_purchase_receipts_request"),
    Index("ix_purchase_receipts_1", "business_id", "purchase_order_id", "received_at"),
    ForeignKeyConstraint(
        ["business_id", "purchase_order_id"],
        ["purchase_orders.business_id", "purchase_orders.id"],
        name="fk_purchase_receipts_purchase_order_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "received_by_user_id"], ["users.business_id", "users.id"],
        name="fk_purchase_receipts_received_by_user_id",
    ),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)


purchase_receipt_items = db.Table(
    "purchase_receipt_items",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), ForeignKey(
        "businesses.id", name="fk_purchase_receipt_items_business_id"
    ), nullable=False),
    Column("purchase_receipt_id", _identifier_type(), nullable=False),
    Column("purchase_order_item_id", _identifier_type(), nullable=False),
    Column("received_qty", Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql"),
           nullable=False),
    UniqueConstraint("business_id", "id", name="uq_purchase_receipt_items_bid_id"),
    UniqueConstraint("purchase_receipt_id", "purchase_order_item_id",
                     name="uq_purchase_receipt_items_line"),
    CheckConstraint("received_qty > 0", name="ck_purchase_receipt_items_qty"),
    ForeignKeyConstraint(
        ["business_id", "purchase_receipt_id"],
        ["purchase_receipts.business_id", "purchase_receipts.id"],
        name="fk_purchase_receipt_items_purchase_receipt_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "purchase_order_item_id"],
        ["purchase_order_items.business_id", "purchase_order_items.id"],
        name="fk_purchase_receipt_items_purchase_order_item_id",
    ),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)
