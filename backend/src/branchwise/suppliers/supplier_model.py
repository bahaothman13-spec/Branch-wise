"""Candidate Core table mapping of the supplied original supplier DDL.

Uses the shared Flask-SQLAlchemy metadata. Importing this module performs no
reflection, connection, DDL, or application installation. The business FK is a
reference to the existing table, not a competing partial businesses model.
"""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, FetchedValue,
    ForeignKey, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects import mysql

from db import db


def _identifier_type():
    return BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql").with_variant(
        Integer(), "sqlite"
    )


def _timestamp_type():
    return DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


suppliers = db.Table(
    "suppliers",
    Column("id", _identifier_type(), primary_key=True, autoincrement=True),
    Column("business_id", _identifier_type(), ForeignKey(
        "businesses.id", name="fk_suppliers_business_id"
    ), nullable=False),
    Column("name", String(160), nullable=False),
    Column("contact_person", String(160)),
    Column("phone", String(30)),
    Column("email", String(254)),
    Column("address", String(255)),
    Column("notes", Text()),
    Column("is_active", Boolean(), nullable=False, server_default=text("1")),
    Column("created_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6)")),
    Column("updated_at", _timestamp_type(), nullable=False,
           server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
           server_onupdate=FetchedValue()),
    UniqueConstraint("business_id", "id", name="uq_suppliers_bid_id"),
    UniqueConstraint("business_id", "name", name="uq_suppliers_name"),
    CheckConstraint("CHAR_LENGTH(TRIM(name)) > 0", name="ck_suppliers_name"),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
)
