"""Tests for SQLAlchemy configuration that do not require a MySQL server."""

from unittest.mock import Mock

import mysql.connector
import pytest
from sqlalchemy.engine import make_url

import db


@pytest.fixture(autouse=True)
def isolated_database_settings(monkeypatch, tmp_path):
    """Use a temporary .env file and keep developer settings out of these tests."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(db, "ENV_FILE", env_file)
    for name in (*db.REQUIRED_SETTINGS, "DB_PORT", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return env_file


def test_database_uri_reads_dotenv_and_encodes_credentials(isolated_database_settings):
    isolated_database_settings.write_text(
        "DB_HOST=db.example.test\n"
        "DB_PORT=3307\n"
        "DB_USER=test_user\n"
        "DB_PASSWORD=p@ss:/word\n"
        "DB_NAME=test_database\n",
        encoding="utf-8",
    )

    url = make_url(db.get_database_uri())

    assert url.drivername == "mysql+mysqlconnector"
    assert url.host == "db.example.test"
    assert url.port == 3307
    assert url.username == "test_user"
    assert url.password == "p@ss:/word"
    assert url.database == "test_database"


def test_database_uri_uses_in_memory_sqlite_when_settings_are_missing():
    assert db.get_database_uri() == "sqlite:///:memory:"


def test_database_uri_rejects_incomplete_settings_without_exposing_secrets(
    isolated_database_settings,
):
    isolated_database_settings.write_text(
        "DB_HOST=db.example.test\n"
        "DB_USER=test_user\n"
        "DB_PASSWORD=private_password\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="incomplete") as error:
        db.get_database_uri()

    assert "private_password" not in str(error.value)


def test_database_uri_rejects_invalid_port(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.example.test")
    monkeypatch.setenv("DB_USER", "test_user")
    monkeypatch.setenv("DB_PASSWORD", "test_password")
    monkeypatch.setenv("DB_NAME", "test_database")
    monkeypatch.setenv("DB_PORT", "not-a-port")

    with pytest.raises(ValueError, match="DB_PORT"):
        db.get_database_uri()


def test_creating_flask_app_initializes_orm_without_connecting_to_mysql(monkeypatch):
    from app import create_app

    connect = Mock()
    monkeypatch.setattr(mysql.connector, "connect", connect)
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+mysqlconnector://test_user:test_password@db.example.test:3307/test_database",
    )

    app = create_app()

    assert app.extensions["sqlalchemy"] is db.db
    assert "migrate" in app.extensions
    with app.app_context():
        assert db.db.engine.url.drivername == "mysql+mysqlconnector"

    connect.assert_not_called()
