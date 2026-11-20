"""Disposable SQLite integration tests, plus static MySQL DDL/error tests.

No MySQL server is used: MySQL collation, concurrency and live constraints are
unverified. SQLite receives only portable timestamp defaults and CHAR_LENGTH;
its VARCHAR lengths and default collation do not emulate MySQL.
"""
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

from mysql.connector import IntegrityError as MySQLIntegrityError
import pytest
from sqlalchemy import (
    Column, DefaultClause, Integer, MetaData, Table, Text, UniqueConstraint,
    create_engine, event, func, insert, select, text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable

from app import create_app
from db import db
from permissions import (
    AuthenticatedPrincipal, ResourceScope, Role, SUPPLIERS_MANAGE, SUPPLIERS_VIEW,
)
from repository_contracts import (
    RepositoryAuthorizationError, RepositoryConflictError,
    RepositoryNotFoundError, RepositoryPersistenceError,
)
from session_auth import AuthenticatedSession, SESSION_AUTH_EXTENSION
from branchwise.suppliers.supplier_contracts import (
    SupplierCreateData, SupplierValidationError,
)
from branchwise.suppliers.supplier_model import suppliers
from branchwise.suppliers.supplier_persistence import create_supplier
from branchwise.suppliers.supplier_repository import SQLAlchemySupplierRepository


@pytest.fixture
def actor():
    return AuthenticatedSession(
        23, 1, AuthenticatedPrincipal(
            Role.OWNER, 10, None, frozenset({SUPPLIERS_MANAGE, SUPPLIERS_VIEW})
        ), datetime(2030, 1, 1, tzinfo=timezone.utc), "a" * 64, "b" * 64,
    )


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "suppliers.sqlite").as_posix())

    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("CHAR_LENGTH", 1, len, deterministic=True)

    # Test-only parent and unrelated sentinel; never register production models.
    metadata = MetaData()
    businesses = Table("businesses", metadata, Column("id", Integer, primary_key=True))
    unrelated = Table("unrelated_probe", metadata, Column("value", Text, nullable=False))
    portable = suppliers.to_metadata(metadata)
    for name in ("created_at", "updated_at"):
        portable.c[name].server_default = DefaultClause(text("CURRENT_TIMESTAMP"))
    metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        session.execute(insert(businesses), [{"id": 10}, {"id": 99}])
        session.execute(insert(unrelated).values(value="unchanged"))
    try:
        yield engine, factory, businesses, unrelated
    finally:
        engine.dispose()


@pytest.fixture
def repository(database):
    return SQLAlchemySupplierRepository(database[1])


def count_rows(database):
    with database[1]() as session:
        return session.scalar(select(func.count()).select_from(suppliers))


def persist(actor, repository, data):
    return create_supplier(
        data, actor=actor, scope=ResourceScope(actor.principal.business_id, None),
        repository=repository,
    )


def test_mapping_matches_supplied_mysql_ddl():
    assert suppliers.metadata is db.metadata
    assert set(suppliers.c.keys()) == {
        "id", "business_id", "name", "contact_person", "phone", "email", "address",
        "notes", "is_active", "created_at", "updated_at",
    }
    assert {tuple(column.name for column in constraint.columns)
            for constraint in suppliers.constraints
            if isinstance(constraint, UniqueConstraint)} == {
                ("business_id", "id"), ("business_id", "name"),
            }
    assert {c.name for c in suppliers.c if c.nullable} == {
        "contact_person", "phone", "email", "address", "notes",
    }
    foreign_key = next(iter(suppliers.c.business_id.foreign_keys))
    assert foreign_key.target_fullname == "businesses.id"
    assert foreign_key.ondelete is None and foreign_key.onupdate is None
    metadata = MetaData()
    Table("businesses", metadata, Column("id", mysql.BIGINT(unsigned=True), primary_key=True))
    table = suppliers.to_metadata(metadata)
    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    for fragment in (
        "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT",
        "business_id BIGINT UNSIGNED NOT NULL", "name VARCHAR(160) NOT NULL",
        "contact_person VARCHAR(160)", "phone VARCHAR(30)", "email VARCHAR(254)",
        "address VARCHAR(255)", "notes TEXT",
        "is_active BOOL NOT NULL DEFAULT 1",
        "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
        "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)",
        "UNIQUE (business_id, id)", "UNIQUE (business_id, name)",
        "CHECK (CHAR_LENGTH(TRIM(name)) > 0)",
        "FOREIGN KEY(business_id) REFERENCES businesses (id)",
        "utf8mb4_0900_ai_ci", "CHARSET=utf8mb4", "ENGINE=InnoDB",
    ):
        assert fragment in ddl


def test_create_and_retrieve_across_independent_sessions(actor, repository, database):
    data = {
        "name": "  Supplier  ", "contact_person": " Person ", "phone": "  ext 4 ",
        "email": " orders@example.com ", "address": "", "notes": "  Notes\n",
    }
    saved = persist(actor, repository, data)
    assert saved.supplier_id > 0 and saved.business_id == 10 and saved.is_active is True
    assert saved.created_at is not None and saved.updated_at is not None
    assert {key: getattr(saved, key) for key in data} == data
    assert repository.get_supplier(actor=actor, supplier_id=saved.supplier_id) == saved
    with database[1]() as independent:
        row = independent.execute(select(suppliers)).mappings().one()
        assert row["id"] == saved.supplier_id and row["business_id"] == 10
    assert count_rows(database) == 1


@pytest.mark.parametrize("optional", [
    {}, dict.fromkeys(("contact_person", "phone", "email", "address", "notes")),
    dict.fromkeys(("contact_person", "phone", "email", "address", "notes"), ""),
    dict.fromkeys(("contact_person", "phone", "email", "address", "notes"), " \t "),
])
def test_optional_fields_preserved(actor, repository, optional):
    saved = persist(actor, repository, {"name": "Supplier", **optional})
    for field in ("contact_person", "phone", "email", "address", "notes"):
        assert getattr(saved, field) == optional.get(field)


def test_duplicate_name_rolls_back_and_session_can_be_reused(actor, repository, database):
    first = persist(actor, repository, {"name": "Supplier"})
    with pytest.raises(RepositoryConflictError, match="Supplier name already exists"):
        persist(actor, repository, {"name": "Supplier"})
    assert count_rows(database) == 1
    assert repository.get_supplier(actor=actor, supplier_id=first.supplier_id) == first
    assert persist(actor, repository, {"name": "Another"}).supplier_id != first.supplier_id


def test_foreign_business_lookup_is_indistinguishable_from_missing(actor, repository, database):
    saved = persist(actor, repository, {"name": "Supplier"})
    foreign = replace(actor, principal=replace(actor.principal, business_id=99))
    messages = []
    for supplier_id in (saved.supplier_id, 99999):
        with pytest.raises(RepositoryNotFoundError) as error:
            repository.get_supplier(actor=foreign, supplier_id=supplier_id)
        messages.append(str(error.value))
    assert messages == ["Supplier not found."] * 2
    other = persist(foreign, repository, {"name": "Supplier"})
    assert other.business_id == 99
    assert repository.get_supplier(actor=foreign, supplier_id=other.supplier_id) == other
    assert count_rows(database) == 2


@pytest.mark.parametrize("field,maximum", [
    ("name", 160), ("contact_person", 160), ("phone", 30), ("email", 254),
    ("address", 255), ("notes", 65535),
])
def test_sql_field_limits_before_any_database_work(actor, database, field, maximum):
    factory = Mock(wraps=database[1])
    repository = SQLAlchemySupplierRepository(factory)
    maximum_value = ("x" * (maximum - 12) + "@example.com") if field == "email" else "x" * maximum
    saved = persist(actor, repository, {"name": "Supplier", field: maximum_value})
    assert getattr(saved, field) == maximum_value
    factory.reset_mock()
    with pytest.raises(SupplierValidationError) as error:
        persist(actor, repository, {"name": "Other", field: "x" + maximum_value})
    assert field in error.value.errors
    factory.assert_not_called()
    assert count_rows(database) == 1


def test_text_limit_is_utf8_bytes_and_varchar_limit_is_characters(actor, repository, database):
    notes = "\U0001f600" * 16383 + "abc"
    saved = persist(actor, repository, {"name": "\U0001f600" * 160, "notes": notes})
    assert saved.notes == notes
    with pytest.raises(SupplierValidationError) as error:
        persist(actor, repository, {"name": "Other", "notes": notes + "a"})
    assert "notes" in error.value.errors
    assert count_rows(database) == 1


@pytest.mark.parametrize("data", [
    None, [], {}, {"name": " "}, {"name": 42},
    {"name": "Supplier", "email": "invalid"},
    {"name": "Supplier", "phone": 10},
    {"name": "Supplier", "business_id": 99},
    {"name": "Supplier", "is_active": False},
    {"name": "Supplier", "id": 7},
    {"name": "Supplier", "created_at": "2030-01-01"},
    {"name": "Supplier", "notes": "\ud800"},
])
def test_invalid_input_never_opens_session(actor, database, data):
    factory = Mock(wraps=database[1])
    repository = SQLAlchemySupplierRepository(factory)
    with pytest.raises(SupplierValidationError):
        persist(actor, repository, data)
    factory.assert_not_called()
    assert count_rows(database) == 0


@pytest.mark.parametrize("changes", [
    {"role": Role.BRANCH_MANAGER, "branch_id": 2},
    {"role": Role.EMPLOYEE, "branch_id": 2},
    {"permissions": frozenset()},
])
def test_unauthorized_creation_never_opens_session(actor, database, changes):
    denied = replace(actor, principal=replace(actor.principal, **changes))
    factory = Mock(wraps=database[1])
    repository = SQLAlchemySupplierRepository(factory)
    with pytest.raises(PermissionError):
        persist(denied, repository, {"name": "Supplier"})
    with pytest.raises(RepositoryAuthorizationError):
        repository.create_supplier_atomic(actor=denied, data=SupplierCreateData("Supplier"))
    factory.assert_not_called()
    assert count_rows(database) == 0


@pytest.mark.parametrize("actor_value", [None, {}, "Owner"])
def test_repository_requires_trusted_session(database, actor_value):
    factory = Mock(wraps=database[1])
    repository = SQLAlchemySupplierRepository(factory)
    with pytest.raises(RepositoryAuthorizationError):
        repository.create_supplier_atomic(actor=actor_value, data=SupplierCreateData("Supplier"))
    with pytest.raises(RepositoryAuthorizationError):
        repository.get_supplier(actor=actor_value, supplier_id=1)
    factory.assert_not_called()


def test_foreign_preparation_scope_never_writes(actor, repository, database):
    with pytest.raises(PermissionError):
        create_supplier({"name": "Supplier"}, actor=actor, scope=ResourceScope(99, None),
                        repository=repository)
    assert count_rows(database) == 0


def test_lookup_permissions(actor, repository, database):
    saved = persist(actor, repository, {"name": "Supplier"})
    manager = replace(actor, principal=replace(
        actor.principal, role=Role.BRANCH_MANAGER, branch_id=2,
        permissions=frozenset({SUPPLIERS_VIEW}),
    ))
    assert repository.get_supplier(actor=manager, supplier_id=saved.supplier_id) == saved
    for principal in (
        replace(actor.principal, permissions=frozenset()),
        replace(manager.principal, role=Role.EMPLOYEE),
    ):
        with pytest.raises(RepositoryAuthorizationError):
            repository.get_supplier(actor=replace(actor, principal=principal),
                                    supplier_id=saved.supplier_id)


@pytest.mark.parametrize("supplier_id", [True, 0, -1, "1", None])
def test_invalid_lookup_ids_before_session(actor, database, supplier_id):
    factory = Mock(wraps=database[1])
    repository = SQLAlchemySupplierRepository(factory)
    with pytest.raises(ValueError):
        repository.get_supplier(actor=actor, supplier_id=supplier_id)
    factory.assert_not_called()


def test_repository_revalidates_typed_data(actor, database):
    data = SupplierCreateData("Supplier")
    object.__setattr__(data, "name", "x" * 161)
    factory = Mock(wraps=database[1])
    with pytest.raises(SupplierValidationError):
        SQLAlchemySupplierRepository(factory).create_supplier_atomic(actor=actor, data=data)
    factory.assert_not_called()


def test_database_nonblank_fk_and_uniqueness_constraints(database):
    with database[1].begin() as session:
        session.execute(insert(suppliers).values(name="Supplier", business_id=10))
    for values in (
        {"name": " ", "business_id": 10},
        {"name": "Supplier", "business_id": 10},
        {"name": "Other", "business_id": 1234},
        {"name": None, "business_id": 10},
    ):
        with pytest.raises(IntegrityError):
            with database[1].begin() as session:
                session.execute(insert(suppliers).values(**values))
    assert count_rows(database) == 1


def test_non_duplicate_integrity_failure_is_generic(actor, repository, database):
    foreign = replace(actor, principal=replace(actor.principal, business_id=1234))
    with pytest.raises(RepositoryPersistenceError) as error:
        persist(foreign, repository, {"name": "Supplier"})
    assert str(error.value) == "Supplier could not be saved."
    assert error.value.__suppress_context__ is True
    assert count_rows(database) == 0


@pytest.mark.parametrize("operation", ["insert", "select", "commit"])
def test_failure_rolls_back_insert_without_success(actor, database, operation):
    class FailingSession(Session):
        def execute(self, statement, *args, **kwargs):
            if (operation == "insert" and statement.is_insert
                    or operation == "select" and statement.is_select):
                raise OperationalError("private SQL and credentials", {}, Exception("secret"))
            return super().execute(statement, *args, **kwargs)

    engine = database[0]
    def fail_commit(_connection):
        raise OperationalError("private SQL and credentials", {}, Exception("secret"))
    if operation == "commit":
        event.listen(engine, "commit", fail_commit)
    repository = SQLAlchemySupplierRepository(sessionmaker(bind=engine, class_=FailingSession))
    try:
        with pytest.raises(RepositoryPersistenceError) as error:
            persist(actor, repository, {"name": "Supplier"})
        assert str(error.value) == "Supplier could not be saved."
    finally:
        if operation == "commit":
            event.remove(engine, "commit", fail_commit)
    assert count_rows(database) == 0


def test_lookup_database_failure_is_generic(actor, database):
    factory = Mock(side_effect=OperationalError("private SQL", {}, Exception("secret")))
    with pytest.raises(RepositoryPersistenceError) as error:
        SQLAlchemySupplierRepository(factory).get_supplier(actor=actor, supplier_id=1)
    assert str(error.value) == "Supplier could not be loaded."


@pytest.mark.parametrize("key,expected", [
    ("uq_suppliers_name", RepositoryConflictError),
    ("uq_suppliers_bid_id", RepositoryPersistenceError),
    ("uq_suppliers_name_other", RepositoryPersistenceError),
])
def test_mysql_duplicate_error_translation_only_unit_simulation(actor, database, key, expected):
    original = MySQLIntegrityError(msg=f"Duplicate entry for key 'suppliers.{key}'",
                                             errno=1062)
    factory = Mock(side_effect=IntegrityError("private SQL", {}, original))
    with pytest.raises(expected) as error:
        persist(actor, SQLAlchemySupplierRepository(factory), {"name": "Supplier"})
    assert "private" not in str(error.value) and key not in str(error.value)
    assert count_rows(database) == 0


def test_rejects_caller_transaction_without_rolling_it_back(actor, database):
    with database[1]() as borrowed:
        borrowed.execute(insert(database[3]).values(value="caller-owned"))
        repository = SQLAlchemySupplierRepository(lambda: borrowed)
        with pytest.raises(RepositoryPersistenceError, match="fresh"):
            persist(actor, repository, {"name": "Supplier"})
        assert borrowed.in_transaction()
        assert borrowed.scalar(select(func.count()).select_from(database[3])) == 2
        borrowed.rollback()
    assert count_rows(database) == 0


def test_no_unrelated_table_writes(actor, repository, database):
    persist(actor, repository, {"name": "Supplier"})
    with database[1]() as session:
        assert session.execute(select(database[2])).all() == [(10,), (99,)]
        assert session.execute(select(database[3])).all() == [("unchanged",)]


def test_imports_do_not_install_adapter_or_supplier_route():
    app = create_app({"TESTING": True, "APP_ENV": "test",
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    assert not any("supplier" in str(rule) or "suppliers." in rule.endpoint
                   for rule in app.url_map.iter_rules())
    assert SESSION_AUTH_EXTENSION not in app.extensions
    assert not any(isinstance(value, SQLAlchemySupplierRepository)
                   for value in app.extensions.values())
    assert app.test_client().post("/api/suppliers", json={"name": "Supplier"}).status_code == 404
