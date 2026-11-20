"""Core mappings of the approved purchase-order DDL; no DDL is executed.

Uses the existing db.metadata and supplier mapping's MySQL type conventions.
Parent FKs reference the existing schema; no partial parent models are added.
"""
from sqlalchemy import (
    CheckConstraint, Column, DECIMAL, Enum, FetchedValue, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.dialects import mysql

from db import db
from branchwise.suppliers.supplier_model import _identifier_type, _timestamp_type


purchase_orders = db.Table(
    "purchase_orders",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), ForeignKey(
        "businesses.id", name="fk_purchase_orders_business_id"
    ), nullable=False),
    Column("branch_id", _identifier_type(), nullable=False),
    Column("supplier_id", _identifier_type(), nullable=False),
    Column("purchase_reference", String(40), nullable=False),
    Column("status", Enum("PENDING", "PARTIALLY_RECEIVED", "RECEIVED"),
           nullable=False, server_default=text("'PENDING'")),
    Column("ordered_by_user_id", _identifier_type(), nullable=False),
    Column("ordered_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6)")),
    Column("expected_total_cost", DECIMAL(14, 2, asdecimal=True), nullable=False),
    Column("created_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6)")),
    Column("updated_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
           server_onupdate=FetchedValue()),
    UniqueConstraint("business_id", "id", name="uq_purchase_orders_bid_id"),
    UniqueConstraint("business_id", "purchase_reference", name="uq_purchase_orders_reference"),
    Index("ix_purchase_orders_1", "business_id", "branch_id", "ordered_at", "status"),
    Index("ix_purchase_orders_2", "business_id", "supplier_id", "ordered_at"),
    CheckConstraint("expected_total_cost >= 0", name="ck_purchase_orders_cost"),
    ForeignKeyConstraint(
        ["business_id", "branch_id"], ["branches.business_id", "branches.id"],
        name="fk_purchase_orders_branch_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "supplier_id"], ["suppliers.business_id", "suppliers.id"],
        name="fk_purchase_orders_supplier_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "ordered_by_user_id"], ["users.business_id", "users.id"],
        name="fk_purchase_orders_ordered_by_user_id",
    ),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)


purchase_order_items = db.Table(
    "purchase_order_items",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), ForeignKey(
        "businesses.id", name="fk_purchase_order_items_business_id"
    ), nullable=False),
    Column("purchase_order_id", _identifier_type(), nullable=False),
    Column("variant_id", _identifier_type(), nullable=False),
    Column("ordered_qty", Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql"),
           nullable=False),
    Column("unit_cost", DECIMAL(14, 2, asdecimal=True), nullable=False),
    Column("line_total", DECIMAL(14, 2, asdecimal=True), nullable=False),
    UniqueConstraint("business_id", "id", name="uq_purchase_order_items_bid_id"),
    UniqueConstraint("purchase_order_id", "variant_id", name="uq_purchase_order_items_variant"),
    Index("ix_purchase_order_items_1", "business_id", "variant_id"),
    CheckConstraint("ordered_qty > 0", name="ck_purchase_order_items_qty"),
    CheckConstraint("unit_cost >= 0 AND line_total = ROUND(ordered_qty * unit_cost, 2)",
                    name="ck_purchase_order_items_total"),
    ForeignKeyConstraint(
        ["business_id", "purchase_order_id"],
        ["purchase_orders.business_id", "purchase_orders.id"],
        name="fk_purchase_order_items_purchase_order_id",
    ),
    ForeignKeyConstraint(
        ["business_id", "variant_id"],
        ["product_variants.business_id", "product_variants.id"],
        name="fk_purchase_order_items_variant_id",
    ),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)
